"""
Simulation runtime: the "field bus" between the plant and the PLC.

Each `step()` performs one complete scan cycle, respecting the data-flow
ordering of a real PLC:

    field sensors  ->  PLC input image  ->  program scan  ->  output image
          ->  field actuators  ->  plant physics  ->  (next scan) ...

The runtime also models *field device* behaviour that a PLC cannot control
directly: pump trips, stuck motorised valves, blocked outlets and transmitter
faults.  These are injected here (as they would be by a maintenance/diagnostic
tool or a physical failure) and observed by the PLC through its I/O image.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from . import config
from .historian import TrendStore
from .leakdetect import MassBalanceLeakDetector, _Balance
from .leaks import LeakStore
from .plc import PLC
from .process import Plant
from .process_units import ProcessUnits

# Flow feedback threshold: below this the pump run switch reads "not running".
PUMP_FB_THRESHOLD = 0.001  # m^3/s


class Runtime:
    def __init__(self, leak_store: LeakStore | None = None,
                 historian: TrendStore | None = None) -> None:
        # Serialises the sim-loop thread against the Modbus server thread so
        # a remote register write never interleaves with a PLC scan.
        self.field_lock = threading.RLock()

        self.plant = Plant()
        self.plc = PLC()
        self.units = ProcessUnits()  # heat exchanger, pressure vessel, conveyor

        # Durable trend persistence (optional: None disables it, e.g. tests).
        self.historian = historian
        self._pending_history: list[dict] = []

        # Field-device fault state.
        self.pump_tripped = False
        self.valve_stuck: dict[str, bool] = {t: False for t in ("XV-101", "XV-102", "XV-103")}
        self.valve_stuck_pos: dict[str, float] = {t: 0.0 for t in self.valve_stuck}
        self.leaks: dict[str, float] = {t: 0.0 for t in config.TANKS}
        self.valve_leaks: dict[str, float] = {t: 0.0
                                              for t in ("XV-101", "XV-102", "XV-103")}

        # Leak detection & persistent event log.
        self.leak_store = leak_store if leak_store is not None else LeakStore()
        self.leak_detector = MassBalanceLeakDetector()
        self._active_leaks: dict[str, str] = {}   # tank tag -> leak event id
        self._last_leak_rate: dict[str, float] = {t: 0.0 for t in config.TANKS}

        # Timed disturbance bookkeeping.
        self._disturbance_until = 0.0
        self._disturbance_tank = "TK-102"
        self._disturbance_flow = 0.004

        # Trending history (full resolution, downsampled by the server).
        self.history: deque[dict] = deque(maxlen=20000)

    # ------------------------------------------------------------------
    # Scan cycle
    # ------------------------------------------------------------------
    def step(self, dt: float = config.PLC_SCAN_DT) -> dict:
        """Advance one PLC scan cycle (serialised against the field bus)."""
        with self.field_lock:
            return self._step(dt)

    def _step(self, dt: float) -> dict:
        """One scan cycle.  The caller must hold ``field_lock``."""
        plc = self.plc
        plant = self.plant

        # 1) INPUT SCAN: sample field instrumentation into the input image.
        readings = plant.read_transmitters(dt)
        for tag, value in readings.items():
            plc.ai[tag] = value

        # Pump run feedback = motor auxiliary contact: energised while the
        # previous scan's run coil is set and the motor has not tripped.
        # (A real PLC reads the contactor, not the flow; flow is telemetry.)
        pump_energised = plc.q["Q0.0_PUMP"] and not self.pump_tripped
        plc.set_pump_feedback(pump_energised)
        # Valve position feedback comes from the *actual* (slew-limited)
        # actuator position, as a position transmitter would report it.
        for vtag in self.valve_stuck:
            plc.set_valve_feedback(vtag, self._valve_position_pct(vtag))

        # Additional process unit transmitters into PLC input image.
        plc.ai["TT-101"] = self.units.hx.cold_out
        plc.ai["PT-101"] = self.units.pressure.pressure

        # 2) PROGRAM SCAN + OUTPUT UPDATE.
        plc.scan(dt)

        # 3) FIELD ACTUATION: apply outputs, with field faults overlaid.
        pump_cmd = plc.aq["P-101"] / 100.0
        if self.pump_tripped:
            eff_pump = 0.0
        else:
            eff_pump = pump_cmd

        eff_valves = {}
        for vtag in ("XV-101", "XV-102", "XV-103"):
            cmd = plc.aq[vtag] / 100.0
            if self.valve_stuck[vtag]:
                eff_valves[vtag] = self.valve_stuck_pos[vtag]
            else:
                eff_valves[vtag] = cmd

        plant.set_actuators(pump_speed=eff_pump, valve_open=eff_valves)

        # 4) Apply process unit actuators from PLC output image.
        fv101_cmd = plc.aq["FV-101"] / 100.0
        self.units.hx.flow_cold = fv101_cmd * 0.002  # max cold flow 0.002 m^3/s
        pcv101_cmd = plc.aq["PCV-101"] / 100.0
        self.units.pressure.valve_position = pcv101_cmd

        # Apply leaks (process disturbances) to the plant.
        plant.leaks = dict(self.leaks)
        plant.valve_leaks = dict(self.valve_leaks)
        if self.plant.t < self._disturbance_until:
            plant.leaks[self._disturbance_tank] = self._disturbance_flow

        # 4) PLANT PHYSICS advance.
        plant.step(dt)
        self.units.step(dt)  # advance additional process units

        # 5) LEAK DETECTION: reconcile each tank's mass balance.
        self._detect_leaks(dt)

        self._record_history()
        return self.snapshot()

    def _valve_position_pct(self, tag: str) -> float:
        """Actual valve position (%) as read by a position transmitter."""
        return self.plant.valves[tag].open_frac * 100.0

    # ------------------------------------------------------------------
    # Leak detection
    # ------------------------------------------------------------------
    def _tank_flows(self, tag: str) -> tuple[float, float]:
        """Measured inflow/outflow (m^3/s) for a tank from plant telemetry."""
        f = self.plant.flows
        if tag == "TK-101":
            return f.get("P-101", 0.0), f.get("XV-101", 0.0)
        if tag == "TK-102":
            return f.get("XV-101", 0.0), f.get("XV-102", 0.0)
        if tag == "TK-103":
            return f.get("XV-102", 0.0), f.get("XV-103", 0.0)
        return 0.0, 0.0

    def _detect_leaks(self, dt: float) -> None:
        """Run the mass-balance detector and raise/resolve leak events + alarms."""
        for tag, tank in self.plant.tanks.items():
            q_in, q_out = self._tank_flows(tag)
            rate = self.leak_detector.update(
                tag, level=self.plant.levels[tag], q_in=q_in, q_out=q_out,
                overflow_volume=self.plant.overflow_volume[tag], dt=dt,
                t=self.plant.t, area=tank.area)
            self._last_leak_rate[tag] = rate
            n = tag[-3:]
            if rate >= config.LEAK_DETECT_MIN_RATE and tag not in self._active_leaks:
                ev = self.leak_store.record(
                    tank=tag, rate_m3s=rate,
                    level_before=self.plant.levels[tag],
                    source="mass-balance", alarm_tag=f"LK-{n}",
                    t_start=self.plant.t)
                self._active_leaks[tag] = ev.id
                self.plc.alarms.raise_alarm(
                    f"LK-{n}", f"{tag} leak detected (mass balance)",
                    "HIGH", self.plc.t)
            elif rate < config.LEAK_CLEAR_RATE and tag in self._active_leaks:
                self.leak_store.resolve(self._active_leaks.pop(tag),
                                        t_end=self.plant.t)
                self.plc.alarms.clear_alarm(f"LK-{n}", self.plc.t)

    # ------------------------------------------------------------------
    # Trending
    # ------------------------------------------------------------------
    def _record_history(self) -> None:
        plant = self.plant
        plc = self.plc
        rec = {
            "t": plant.t,
            "h1": plant.levels["TK-101"],
            "h2": plant.levels["TK-102"],
            "h3": plant.levels["TK-103"],
            "lic101_sp": plc.pids["LIC-101"].setpoint,
            "lic101_pv": plc.pids["LIC-101"].pv,
            "lic101_mv": plc.pids["LIC-101"].mv,
            "lic102_sp": plc.pids["LIC-102"].setpoint,
            "lic102_pv": plc.pids["LIC-102"].pv,
            "lic102_mv": plc.pids["LIC-102"].mv,
            "pump_flow": plant.flows.get("P-101", 0.0),
            "v101_flow": plant.flows.get("XV-101", 0.0),
            "v102_flow": plant.flows.get("XV-102", 0.0),
            "v103_flow": plant.flows.get("XV-103", 0.0),
            "overflow": plant.flows.get("overflow", 0.0),
        }
        self.history.append(rec)

        if self.historian is not None:
            self._pending_history.append(rec)
            if len(self._pending_history) >= config.HISTORIAN_FLUSH_SCANS:
                self.flush_historian()

    # ------------------------------------------------------------------
    # Historian
    # ------------------------------------------------------------------
    def flush_historian(self) -> None:
        """Write any buffered full-resolution samples to the durable store."""
        if self.historian is None or not self._pending_history:
            return
        self.historian.insert(self._pending_history)
        self._pending_history.clear()

    # ------------------------------------------------------------------
    # Fault injection (field devices)
    # ------------------------------------------------------------------
    def set_sensor_fault(self, tag: str, fault: str) -> None:
        """Inject a transmitter fault: OK|STUCK|FAIL_HIGH|FAIL_LOW|DRIFT|NAN."""
        if tag in self.plant.level_tx:
            true_level = self.plant.levels[self.plant.level_tx[tag].tank_tag]
            self.plant.level_tx[tag].inject_fault(fault.upper(), true_level)

    def clear_sensor_fault(self, tag: str) -> None:
        if tag in self.plant.level_tx:
            self.plant.level_tx[tag].fault = "OK"
            self.plant.level_tx[tag]._drift_value = 0.0

    def trip_pump(self) -> None:
        self.pump_tripped = True

    def reset_pump(self) -> None:
        self.pump_tripped = False

    def stick_valve(self, tag: str) -> None:
        if tag in self.valve_stuck:
            self.valve_stuck[tag] = True
            self.valve_stuck_pos[tag] = self.plant.valves[tag].open_frac

    def unstick_valve(self, tag: str) -> None:
        if tag in self.valve_stuck:
            self.valve_stuck[tag] = False

    def block_outlet(self, tag: str) -> None:
        if tag in self.plant.valves:
            self.plant.valves[tag].blocked = True

    def unblock_outlet(self, tag: str) -> None:
        if tag in self.plant.valves:
            self.plant.valves[tag].blocked = False

    def set_leak(self, tank: str, flow_m3s: float) -> None:
        if tank in self.leaks:
            self.leaks[tank] = max(0.0, float(flow_m3s))

    def set_valve_leak(self, tag: str, flow_m3s: float) -> None:
        """Leak on a transfer valve/pipe: loss from the valve's upstream node."""
        if tag in self.valve_leaks:
            self.valve_leaks[tag] = max(0.0, float(flow_m3s))

    def clear_valve_leak(self, tag: str) -> None:
        if tag in self.valve_leaks:
            self.valve_leaks[tag] = 0.0

    def inject_disturbance(self, tank: str = "TK-102",
                           flow_m3s: float = 0.004, duration: float = 30.0) -> None:
        """Step-like leak on a tank that auto-clears after `duration` seconds."""
        self._disturbance_tank = tank
        self._disturbance_flow = max(0.0, float(flow_m3s))
        self._disturbance_until = self.plant.t + float(duration)

    # ------------------------------------------------------------------
    # Snapshot for the SCADA layer
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        """Consistent read of the whole plant/PLC state (lock-protected)."""
        with self.field_lock:
            return self._snapshot()

    def _snapshot(self) -> dict:
        """Unlocked snapshot builder.  Caller must hold ``field_lock``."""
        plant = self.plant
        plc = self.plc
        levels = plant.read_levels()

        sensors = {tag: tx.fault for tag, tx in plant.level_tx.items()}
        faults = {
            "pump_tripped": self.pump_tripped,
            "valve_stuck": dict(self.valve_stuck),
            "blocked": {t: v.blocked for t, v in plant.valves.items()},
            "leaks": dict(self.leaks),
            "valve_leaks": dict(self.valve_leaks),
            "disturbance_active": plant.t < self._disturbance_until,
        }

        exp = plc.export()

        # Per-tank attributes (measured or directly derived), so the HMI can
        # display every value without re-deriving physics in the frontend.
        tanks = {}
        for tag, tank in plant.tanks.items():
            q_in, q_out = self._tank_flows(tag)
            tanks[tag] = {
                "level": levels[tag],
                "level_pct": levels[tag] / config.TANK_HEIGHT * 100.0,
                "volume": levels[tag] * tank.area,
                "capacity": tank.capacity,
                "area": tank.area,
                "height": tank.height,
                "z_base": tank.z_base,
                "q_in": q_in,
                "q_out": q_out,
                "net_flow": q_in - q_out,
                "leak_injected": self.leaks.get(tag, 0.0),
                "leak_detected": self._last_leak_rate.get(tag, 0.0),
                "leak_active": tag in self._active_leaks,
                "overflow_volume": plant.overflow_volume.get(tag, 0.0),
            }

        return {
            "t": plant.t,
            "reservoir_level": plant.reservoir_level,
            "state": plc.state,
            "estop": plc.i["I0.0_ESTOP"],
            "levels": levels,
            "levels_pct": {t: levels[t] / config.TANK_HEIGHT * 100.0 for t in levels},
            "tanks": tanks,
            "flows": {k: v for k, v in plant.flows.items()},
            "pump": {
                "cmd": plc.aq["P-101"],
                "eff": plant.pumps["P-101"].speed * 100.0,  # actual speed
                "running": plc.q["Q0.0_PUMP"],
                "feedback": plc.i["I0.4_PUMP_FB"],
            },
            "valves": {
                tag: {
                    "cmd": plc.aq[tag],
                    "eff": plant.valves[tag].open_frac * 100.0,  # actual pos
                    "manual": plc.manual_valves.get(tag, None),
                    "blocked": plant.valves[tag].blocked,
                }
                for tag in ("XV-101", "XV-102", "XV-103")
            },
            "pids": exp["pids"],
            "loop_mode": plc.loop_mode,
            "manual_pump": plc.manual_pump,
            "sensors": sensors,
            "sensor_readings": {f"LT-{n}": plc.ai.get(f"LT-{n}", 0.0)
                                for n in (101, 102, 103)},
            "faults": faults,
            "leaks": {
                "injected": dict(self.leaks),
                "disturbance_active": plant.t < self._disturbance_until,
                "active": {tag: self._last_leak_rate.get(tag, 0.0)
                           for tag in self._active_leaks},
            },
            "alarms": exp["alarms"],
            "alarm_history": exp["alarm_history"],
            "interlocks": exp["interlocks"],
            "coils": plc.q,
            "scan_count": plc.scan_count,
            "process_units": self.units.read(),
            "plc": {
                "state": plc.state,
                "scan_count": plc.scan_count,
                "ai": dict(plc.ai),          # analog inputs (engineering units)
                "di": dict(plc.i),           # digital inputs
                "aq": dict(plc.aq),          # analog outputs (%)
                "dq": dict(plc.q),           # digital outputs
                "fbs": {
                    "start_timer_et": plc._start_timer.et,
                    "start_timer_q": plc._start_timer.q,
                    "stop_timer_et": plc._stop_timer.et,
                    "pump_trip_timer_et": plc._pump_trip_timer.et,
                    "valve_fault_timer_et": plc._valve_fault_timer.et,
                    "pump_cycles": plc._pump_cycle_ctr.cv,
                    "trip_latch": plc._trip_latch.q,
                    "valve_fault_latch": plc._valve_fault_latch.q,
                    "sensor_fault_latches": {
                        tag: latch.q for tag, latch in plc._sensor_fault_latch.items()
                    },
                },
                "loop_mode": dict(plc.loop_mode),
                "manual_valves": dict(plc.manual_valves),
                "manual_pump": plc.manual_pump,
            },
        }

    # ------------------------------------------------------------------
    # HA: authoritative-state serialisation (for failover/checkpoints)
    # ------------------------------------------------------------------
    @staticmethod
    def _rng_state_to_json(state: dict) -> dict:
        """Convert a numpy Generator state (may contain ndarrays) to plain
        JSON-safe types so it round-trips through the checkpoint store."""
        import numpy as np
        out = {}
        for k, v in state.items():
            if isinstance(v, dict):
                out[k] = Runtime._rng_state_to_json(v)
            elif isinstance(v, np.ndarray):
                out[k] = {"__ndarray__": v.tolist(),
                          "dtype": str(v.dtype)}
            else:
                out[k] = v
        return out

    @staticmethod
    def _rng_state_from_json(state: dict) -> dict:
        """Inverse of ``_rng_state_to_json``: rebuild numpy arrays."""
        import numpy as np
        out = {}
        for k, v in state.items():
            if isinstance(v, dict) and "__ndarray__" in v:
                out[k] = np.array(v["__ndarray__"], dtype=v["dtype"])
            elif isinstance(v, dict):
                out[k] = Runtime._rng_state_from_json(v)
            else:
                out[k] = v
        return out

    def serialize(self) -> dict:
        """Capture the complete authoritative state as a JSON-serialisable
        dict (plant, PLC engine incl. every function block, process units,
        fault state, disturbance bookkeeping, leak balances, trend tail).

        This is the payload written to the HA checkpoint store; a standby
        that takes over calls ``restore()`` with it and continues the
        simulation seamlessly."""
        plc = self.plc
        plant = self.plant

        # PID internals (anti-windup integrator, derivative state, mode).
        pids = {}
        for tag, pid in plc.pids.items():
            pids[tag] = {
                "sp": pid.setpoint, "pv": pid.pv, "mv": pid.mv,
                "mode": pid.mode, "kp": pid.kp, "ki": pid.ki, "kd": pid.kd,
                "_integral": pid._integral, "_last_pv": pid._last_pv,
                "_deriv": pid._deriv, "p_term": pid.p_term,
                "i_term": pid.i_term, "d_term": pid.d_term,
                "saturated": pid.saturated, "error": pid.error,
            }

        alarms = [self.plc._alarm_dict(a) for a in plc.alarms.history]
        alarm_active = {tag: self.plc._alarm_dict(a)
                        for tag, a in plc.alarms.active.items()}

        return {
            "plant": {
                "t": plant.t,
                "levels": dict(plant.levels),
                "reservoir_level": plant.reservoir_level,
                "overflow_volume": dict(plant.overflow_volume),
                "leaks": dict(plant.leaks),
                "valve_leaks": dict(plant.valve_leaks),
                "valves": {tag: {"open_frac": v.open_frac,
                                  "target": v.target, "blocked": v.blocked}
                           for tag, v in plant.valves.items()},
                "pumps": {tag: {"speed": p.speed, "target": p.target}
                          for tag, p in plant.pumps.items()},
                "transmitters": {tag: {"fault": tx.fault,
                                        "_stuck_value": tx._stuck_value,
                                        "_drift_value": tx._drift_value}
                                 for tag, tx in plant.level_tx.items()},
                "rng": self._rng_state_to_json(plant.rng.bit_generator.state),
            },
            "plc": {
                "state": plc.state, "t": plc.t, "scan_count": plc.scan_count,
                "first_scan": plc.first_scan,
                "ai": dict(plc.ai), "i": dict(plc.i),
                "aq": dict(plc.aq), "q": dict(plc.q), "m": dict(plc.m),
                "loop_mode": dict(plc.loop_mode),
                "loop_manual_mv": dict(plc.loop_manual_mv),
                "manual_valves": dict(plc.manual_valves),
                "manual_pump": plc.manual_pump,
                "cascade_inner_sp": plc.cascade_inner_sp,
                "pids": pids,
                "alarms_active": alarm_active,
                "alarms_history": alarms,
                "fbs": {
                    "start_timer": [plc._start_timer.et, plc._start_timer.q],
                    "stop_timer": [plc._stop_timer.et, plc._stop_timer.q],
                    "pump_trip_timer": [plc._pump_trip_timer.et,
                                         plc._pump_trip_timer.q],
                    "valve_fault_timer": [plc._valve_fault_timer.et,
                                           plc._valve_fault_timer.q],
                    "pump_cycle_ctr": [plc._pump_cycle_ctr.cv,
                                        plc._pump_cycle_ctr._in_prev],
                    "trip_latch": plc._trip_latch.q,
                    "valve_fault_latch": plc._valve_fault_latch.q,
                    "sensor_fault_latches": {
                        tag: latch.q
                        for tag, latch in plc._sensor_fault_latch.items()},
                    "trig_start": plc._trig_start._prev,
                    "trig_stop": plc._trig_stop._prev,
                    "trig_reset": plc._trig_reset._prev,
                    "last_level": dict(plc._last_level),
                },
            },
            "units": {
                "hx": {"_T_hot": self.units.hx._T_hot,
                        "_T_cold": self.units.hx._T_cold,
                        "fouling": self.units.hx.fouling,
                        "flow_hot": self.units.hx.flow_hot,
                        "flow_cold": self.units.hx.flow_cold},
                "pressure": {"_P": self.units.pressure._P,
                              "valve_position": self.units.pressure.valve_position},
                "conveyor": {"speed": self.units.conveyor.speed,
                              "target_speed": self.units.conveyor.target_speed,
                              "running": self.units.conveyor.running,
                              "jammed": self.units.conveyor.jammed,
                              "motor_fault": self.units.conveyor.motor_fault,
                              "products_on_belt": self.units.conveyor.products_on_belt,
                              "runtime_accum": self.units.conveyor.runtime_accum,
                              "jam_timer": self.units.conveyor.jam_timer},
            },
            "runtime": {
                "pump_tripped": self.pump_tripped,
                "valve_stuck": dict(self.valve_stuck),
                "valve_stuck_pos": dict(self.valve_stuck_pos),
                "leaks": dict(self.leaks),
                "valve_leaks": dict(self.valve_leaks),
                "_disturbance_until": self._disturbance_until,
                "_disturbance_tank": self._disturbance_tank,
                "_disturbance_flow": self._disturbance_flow,
                "_active_leaks": dict(self._active_leaks),
                "_last_leak_rate": dict(self._last_leak_rate),
                "leak_balances": {
                    tag: {"explained": b.explained, "observed": b.observed,
                          "prev_level": b.prev_level,
                          "prev_overflow": b.prev_overflow,
                          "window_start": b.window_start,
                          "last_rate": b.last_rate}
                    for tag, b in self.leak_detector._bal.items()},
                "history": list(self.history),
            },
        }

    def restore(self, state: dict) -> None:
        """Replace this runtime's authoritative state with a previously
        captured ``serialize()`` payload (used on HA takeover).  The
        historian/leak-store objects are *not* part of the payload and are
        left untouched, so durable stores keep their own identity."""
        from .plc import Alarm

        plc = self.plc
        plant = self.plant
        s = state["plant"]

        plant.t = s["t"]
        plant.levels.update(s["levels"])
        plant.reservoir_level = s["reservoir_level"]
        plant.overflow_volume.update(s["overflow_volume"])
        plant.leaks.update(s["leaks"])
        plant.valve_leaks.update(s["valve_leaks"])
        for tag, v in s["valves"].items():
            plant.valves[tag].open_frac = v["open_frac"]
            plant.valves[tag].target = v["target"]
            plant.valves[tag].blocked = v["blocked"]
        for tag, p in s["pumps"].items():
            plant.pumps[tag].speed = p["speed"]
            plant.pumps[tag].target = p["target"]
        for tag, tx in s["transmitters"].items():
            plant.level_tx[tag].fault = tx["fault"]
            plant.level_tx[tag]._stuck_value = tx["_stuck_value"]
            plant.level_tx[tag]._drift_value = tx["_drift_value"]
        plant.rng.bit_generator.state = self._rng_state_from_json(s["rng"])

        p = state["plc"]
        plc.state = p["state"]
        plc.t = p["t"]
        plc.scan_count = p["scan_count"]
        plc.first_scan = p["first_scan"]
        plc.ai.update(p["ai"])
        plc.i.update(p["i"])
        plc.aq.update(p["aq"])
        plc.q.update(p["q"])
        plc.m.update(p["m"])
        plc.loop_mode.update(p["loop_mode"])
        plc.loop_manual_mv.update(p["loop_manual_mv"])
        plc.manual_valves.update(p["manual_valves"])
        plc.manual_pump = p["manual_pump"]
        plc.cascade_inner_sp = p["cascade_inner_sp"]
        for tag, pid in p["pids"].items():
            c = plc.pids[tag]
            c.setpoint = pid["sp"]
            c.pv = pid["pv"]
            c.mv = pid["mv"]
            c.mode = pid["mode"]
            c.kp, c.ki, c.kd = pid["kp"], pid["ki"], pid["kd"]
            c._integral = pid["_integral"]
            c._last_pv = pid["_last_pv"]
            c._deriv = pid["_deriv"]
            c.p_term = pid["p_term"]
            c.i_term = pid["i_term"]
            c.d_term = pid["d_term"]
            c.saturated = pid["saturated"]
            c.error = pid["error"]

        # Rebuild the alarm manager state (active set + history).
        def _mk(d: dict) -> Alarm:
            return Alarm(tag=d["tag"], message=d["message"],
                         priority=d["priority"], active=d.get("active", True),
                         acked=d.get("acked", False), t_raise=d["t_raise"],
                         t_ack=d.get("t_ack"), t_clear=d.get("t_clear"))
        plc.alarms.active = {tag: _mk(d) for tag, d in p["alarms_active"].items()}
        plc.alarms.history = [_mk(d) for d in p["alarms_history"]]

        fb = p["fbs"]
        plc._start_timer.et, plc._start_timer.q = fb["start_timer"]
        plc._stop_timer.et, plc._stop_timer.q = fb["stop_timer"]
        plc._pump_trip_timer.et, plc._pump_trip_timer.q = fb["pump_trip_timer"]
        plc._valve_fault_timer.et, plc._valve_fault_timer.q = fb["valve_fault_timer"]
        plc._pump_cycle_ctr.cv, plc._pump_cycle_ctr._in_prev = fb["pump_cycle_ctr"]
        plc._trip_latch.q = fb["trip_latch"]
        plc._valve_fault_latch.q = fb["valve_fault_latch"]
        for tag, q in fb["sensor_fault_latches"].items():
            plc._sensor_fault_latch[tag].q = q
        plc._trig_start._prev = fb["trig_start"]
        plc._trig_stop._prev = fb["trig_stop"]
        plc._trig_reset._prev = fb["trig_reset"]
        plc._last_level.update(fb["last_level"])

        u = state["units"]
        self.units.hx._T_hot = u["hx"]["_T_hot"]
        self.units.hx._T_cold = u["hx"]["_T_cold"]
        self.units.hx.fouling = u["hx"]["fouling"]
        self.units.hx.flow_hot = u["hx"]["flow_hot"]
        self.units.hx.flow_cold = u["hx"]["flow_cold"]
        self.units.pressure._P = u["pressure"]["_P"]
        self.units.pressure.valve_position = u["pressure"]["valve_position"]
        cv = u["conveyor"]
        self.units.conveyor.speed = cv["speed"]
        self.units.conveyor.target_speed = cv["target_speed"]
        self.units.conveyor.running = cv["running"]
        self.units.conveyor.jammed = cv["jammed"]
        self.units.conveyor.motor_fault = cv["motor_fault"]
        self.units.conveyor.products_on_belt = cv["products_on_belt"]
        self.units.conveyor.runtime_accum = cv["runtime_accum"]
        self.units.conveyor.jam_timer = cv["jam_timer"]

        r = state["runtime"]
        self.pump_tripped = r["pump_tripped"]
        self.valve_stuck.update(r["valve_stuck"])
        self.valve_stuck_pos.update(r["valve_stuck_pos"])
        self.leaks.update(r["leaks"])
        self.valve_leaks.update(r["valve_leaks"])
        self._disturbance_until = r["_disturbance_until"]
        self._disturbance_tank = r["_disturbance_tank"]
        self._disturbance_flow = r["_disturbance_flow"]
        self._active_leaks.update(r["_active_leaks"])
        self._last_leak_rate.update(r["_last_leak_rate"])
        self.leak_detector._bal = {}
        for tag, b in r["leak_balances"].items():
            bal = _Balance()
            bal.explained = b["explained"]
            bal.observed = b["observed"]
            bal.prev_level = b["prev_level"]
            bal.prev_overflow = b["prev_overflow"]
            bal.window_start = b["window_start"]
            bal.last_rate = b["last_rate"]
            self.leak_detector._bal[tag] = bal
        self.history.clear()
        self.history.extend(r["history"])

    def plant_config(self) -> dict:
        """Static plant topology + 3D layout, for a config-driven frontend."""
        tanks = []
        for tag, spec in config.TANKS.items():
            pos = config.LAYOUT.get(tag, {})
            tanks.append({
                "tag": tag,
                "area": spec["area"],
                "height": spec["height"],
                "z_base": spec["z_base"],
                "capacity": spec["area"] * spec["height"],
                "radius": round(math.sqrt(spec["area"] / math.pi), 4),
                "x": pos.get("x", 0.0),
                "z": pos.get("z", 0.0),
            })
        valves = []
        for tag, spec in config.VALVES.items():
            pos = config.LAYOUT.get(tag, {})
            valves.append({
                "tag": tag,
                "upstream": spec["upstream"],
                "downstream": spec["downstream"],
                "kv": spec["kv"],
                "x": pos.get("x", 0.0),
                "y": pos.get("y", 0.0),
                "z": pos.get("z", 0.0),
            })
        loops = [
            {"tag": tag, "pv_tag": spec["pv_tag"], "mv_tag": spec["mv_tag"],
             "setpoint": spec["setpoint"], "kp": spec["kp"], "ki": spec["ki"],
             "kd": spec["kd"]}
            for tag, spec in config.PID_LOOPS.items()
        ]
        return {
            "tank_height": config.TANK_HEIGHT,
            "tanks": tanks,
            "valves": valves,
            "pump": {"tag": "P-101", "max_flow": config.PUMP_MAX_FLOW,
                     "upstream": "reservoir", "downstream": "TK-101",
                     **config.LAYOUT.get("P-101", {})},
            "reservoir": config.LAYOUT.get("reservoir", {}),
            "drain": config.LAYOUT.get("drain", {}),
            "loops": loops,
            "alarms": {
                # level thresholds as fractions of tank height (0..1)
                "level_lo": config.LEVEL_LO,
                "level_hi": config.LEVEL_HI,
                "level_hihi": config.LEVEL_HIHI,
            },
            "rates": {"process": config.PROCESS_DT, "plc_scan": config.PLC_SCAN_DT,
                      "ui": config.UI_REFRESH_DT},
        }

    def trends(self, max_points: int = 400) -> dict:
        """Downsampled history for the live charts."""
        data = list(self.history)
        if len(data) <= max_points:
            return {"t": [d["t"] for d in data],
                    "series": {k: [d.get(k, 0.0) for d in data]
                               for k in data[0].keys() if k != "t"} if data else {}}
        step = len(data) / max_points
        idx = [int(i * step) for i in range(max_points)]
        sampled = [data[i] for i in idx]
        return {"t": [d["t"] for d in sampled],
                "series": {k: [d.get(k, 0.0) for d in sampled]
                           for k in sampled[0].keys() if k != "t"}}
