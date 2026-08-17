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

import time
from collections import deque

from . import config
from .plc import PLC
from .process import Plant

# Flow feedback threshold: below this the pump run switch reads "not running".
PUMP_FB_THRESHOLD = 0.001  # m^3/s


class Runtime:
    def __init__(self) -> None:
        self.plant = Plant()
        self.plc = PLC()

        # Field-device fault state.
        self.pump_tripped = False
        self.valve_stuck: dict[str, bool] = {t: False for t in ("XV-101", "XV-102", "XV-103")}
        self.valve_stuck_pos: dict[str, float] = {t: 0.0 for t in self.valve_stuck}
        self.leaks: dict[str, float] = {t: 0.0 for t in config.TANKS}

        # Timed disturbance bookkeeping.
        self._disturbance_until = 0.0
        self._disturbance_tank = "TK-102"
        self._disturbance_flow = 0.004

        # Trending history (full resolution, downsampled by the server).
        self.history: deque[dict] = deque(maxlen=20000)

        self._last_effective_pump = 0.0
        self._last_effective_valves = {t: self.plant.valves[t].open_frac
                                       for t in self.plant.valves}

    # ------------------------------------------------------------------
    # Scan cycle
    # ------------------------------------------------------------------
    def step(self, dt: float = config.PLC_SCAN_DT) -> dict:
        """Advance one PLC scan cycle."""
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
        for vtag in self.valve_stuck:
            plc.set_valve_feedback(vtag, self._valve_position_pct(vtag))

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

        # Apply leaks (process disturbances) to the plant.
        plant.leaks = dict(self.leaks)
        if self.plant.t < self._disturbance_until:
            plant.leaks[self._disturbance_tank] = self._disturbance_flow

        # 4) PLANT PHYSICS advance.
        plant.step(dt)

        self._last_effective_pump = eff_pump
        self._last_effective_valves = dict(eff_valves)

        self._record_history()
        return self.snapshot()

    def _valve_position_pct(self, tag: str) -> float:
        """Effective valve position (%) for feedback telemetry."""
        return self._last_effective_valves.get(tag, 0.0) * 100.0

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
            self.valve_stuck_pos[tag] = self._last_effective_valves.get(tag, 0.0)

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
        plant = self.plant
        plc = self.plc
        levels = plant.read_levels()

        sensors = {tag: tx.fault for tag, tx in plant.level_tx.items()}
        faults = {
            "pump_tripped": self.pump_tripped,
            "valve_stuck": dict(self.valve_stuck),
            "blocked": {t: v.blocked for t, v in plant.valves.items()},
            "leaks": dict(self.leaks),
            "disturbance_active": plant.t < self._disturbance_until,
        }

        return {
            "t": plant.t,
            "state": plc.state,
            "estop": plc.i["I0.0_ESTOP"],
            "levels": levels,
            "levels_pct": {t: levels[t] / config.TANK_HEIGHT * 100.0 for t in levels},
            "flows": {k: v for k, v in plant.flows.items()},
            "pump": {
                "cmd": plc.aq["P-101"],
                "eff": self._last_effective_pump * 100.0,
                "running": plc.q["Q0.0_PUMP"],
                "feedback": plc.i["I0.4_PUMP_FB"],
            },
            "valves": {
                tag: {
                    "cmd": plc.aq[tag],
                    "eff": self._last_effective_valves.get(tag, 0.0) * 100.0,
                    "manual": plc.manual_valves.get(tag, None),
                    "blocked": plant.valves[tag].blocked,
                }
                for tag in ("XV-101", "XV-102", "XV-103")
            },
            "pids": plc.export()["pids"],
            "loop_mode": plc.loop_mode,
            "manual_pump": plc.manual_pump,
            "sensors": sensors,
            "faults": faults,
            "alarms": plc.export()["alarms"],
            "alarm_history": plc.export()["alarm_history"],
            "coils": plc.q,
            "scan_count": plc.scan_count,
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
