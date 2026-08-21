"""
IEC 61131-3 style PLC engine: function blocks, scan cycle, alarms,
interlocks, permissives and an ISA-88 operating state machine.

The PLC is *pure control logic* operating on an input-image table and
producing an output-image table, mirroring a real PLC scan cycle:

    1. INPUT SCAN   - latch field values into the input image (I / AI)
    2. PROGRAM SCAN - execute the program (networks/rungs) in order
    3. OUTPUT UPDATE - write the output image (Q / AQ) to the field

The "field" (plant + actuators + transmitters) lives outside this class; the
runtime copies transmitter readings into the input image before each scan and
applies the output image to the plant afterwards, exactly as a PLC cannot
see its outputs change until the next scan.

Where the code is an *approximation* of industrial hardware (e.g. the
transmitter fault model) rather than a certified device, that is documented
inline or in the README.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import config
from .pid import PIDController


# ===========================================================================
# IEC 61131-3 function blocks
# ===========================================================================
class TON:
    """Timer ON-delay: output goes TRUE `pt` seconds after input rises."""

    def __init__(self, pt: float):
        self.pt = pt
        self.et = 0.0
        self.q = False

    def reset(self) -> None:
        self.et = 0.0
        self.q = False

    def update(self, in_: bool, dt: float) -> bool:
        if in_:
            self.et = min(self.et + dt, self.pt)
        else:
            self.et = 0.0
        self.q = in_ and self.et >= self.pt - 1e-9
        return self.q


class TOF:
    """Timer OFF-delay: output stays TRUE for `pt` seconds after input drops."""

    def __init__(self, pt: float):
        self.pt = pt
        self.et = 0.0
        self.q = False

    def reset(self) -> None:
        self.et = 0.0
        self.q = False

    def update(self, in_: bool, dt: float) -> bool:
        if in_:
            self.et = 0.0
            self.q = True
        elif self.q:
            self.et += dt
            if self.et >= self.pt - 1e-9:
                self.et = self.pt
                self.q = False
        return self.q


class TP:
    """Timer pulse: output is TRUE for `pt` seconds on the rising edge."""

    def __init__(self, pt: float):
        self.pt = pt
        self.et = 0.0
        self.q = False
        self._in_prev = False

    def update(self, in_: bool, dt: float) -> bool:
        if in_ and not self._in_prev:
            self.et = 0.0
            self.q = True
        if self.q:
            self.et += dt
            if self.et >= self.pt:
                self.q = False
        self._in_prev = in_
        return self.q


class CTU:
    """Counter UP: increments on the rising edge; Q when CV >= PV."""

    def __init__(self, pv: int):
        self.pv = pv
        self.cv = 0
        self.q = False
        self._in_prev = False

    def update(self, in_: bool, reset: bool = False) -> bool:
        if reset:
            self.cv = 0
            self.q = False
        elif in_ and not self._in_prev:
            self.cv += 1
            self.q = self.cv >= self.pv
        self._in_prev = in_
        return self.q


class CTD:
    """Counter DOWN: decrements on the rising edge; Q when CV <= 0."""

    def __init__(self, pv: int):
        self.pv = pv
        self.cv = pv
        self.q = False
        self._in_prev = False

    def update(self, in_: bool, load: bool = False, load_value: int | None = None) -> bool:
        if load:
            self.cv = self.pv if load_value is None else load_value
        elif in_ and not self._in_prev:
            self.cv = max(0, self.cv - 1)
        self.q = self.cv <= 0
        self._in_prev = in_
        return self.q


class SR:
    """Set-dominant bistable (IEC 61131-3 SR)."""

    def __init__(self, q: bool = False):
        self.q = q

    def update(self, s: bool, r: bool) -> bool:
        if s:
            self.q = True
        elif r:
            self.q = False
        return self.q


class RS:
    """Reset-dominant bistable (IEC 61131-3 RS) - the safety convention."""

    def __init__(self, q: bool = False):
        self.q = q

    def update(self, s: bool, r: bool) -> bool:
        if r:
            self.q = False
        elif s:
            self.q = True
        return self.q


class RTrig:
    """Rising-edge detector."""

    def __init__(self):
        self._prev = False

    def update(self, in_: bool) -> bool:
        out = in_ and not self._prev
        self._prev = in_
        return out


class FTrig:
    """Falling-edge detector."""

    def __init__(self):
        self._prev = False

    def update(self, in_: bool) -> bool:
        out = (not in_) and self._prev
        self._prev = in_
        return out


# ===========================================================================
# Alarms & events
# ===========================================================================
PRIORITY = {"INFO": 0, "WARNING": 1, "HIGH": 2, "CRITICAL": 3}


@dataclass
class Alarm:
    tag: str
    message: str
    priority: str = "WARNING"
    active: bool = True
    acked: bool = False
    t_raise: float = 0.0
    t_ack: float | None = None
    t_clear: float | None = None

    @property
    def state(self) -> str:
        if self.active:
            return "ACKED" if self.acked else "ACTIVE"
        return "CLEARED"


class AlarmManager:
    """Latched alarm handling with acknowledge/clear semantics."""

    def __init__(self, max_history: int = 200) -> None:
        self.active: dict[str, Alarm] = {}
        self.history: list[Alarm] = []
        self.max_history = max_history
        # Optional observer for durable alarm journaling (RAISE/ACK/CLEAR).
        self._event_cb = None

    def set_event_callback(self, cb) -> None:
        """Register ``cb(action, alarm, t)`` called on each alarm transition."""
        self._event_cb = cb

    def _emit(self, action: str, alarm: Alarm, t: float) -> None:
        if self._event_cb is not None:
            try:
                self._event_cb(action, alarm, t)
            except Exception:
                pass  # a failing observer must never break alarm handling

    def raise_alarm(self, tag: str, message: str, priority: str,
                    t: float, auto_ack: bool = False) -> None:
        if tag in self.active:
            existing = self.active[tag]
            existing.message = message
            existing.priority = priority
            return
        alarm = Alarm(tag=tag, message=message, priority=priority,
                      t_raise=t, acked=auto_ack)
        self.active[tag] = alarm
        self.history.append(alarm)
        self._trim()
        self._emit("RAISE", alarm, t)

    def clear_alarm(self, tag: str, t: float) -> None:
        alarm = self.active.pop(tag, None)
        if alarm is not None:
            alarm.active = False
            alarm.t_clear = t
            self._emit("CLEAR", alarm, t)

    def acknowledge(self, tag: str, t: float) -> bool:
        alarm = self.active.get(tag)
        if alarm is not None and not alarm.acked:
            alarm.acked = True
            alarm.t_ack = t
            self._emit("ACK", alarm, t)
            return True
        return False

    def acknowledge_all(self, t: float) -> int:
        n = 0
        for alarm in self.active.values():
            if not alarm.acked:
                alarm.acked = True
                alarm.t_ack = t
                self._emit("ACK", alarm, t)
                n += 1
        return n

    def _trim(self) -> None:
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def active_alarms(self) -> list[Alarm]:
        return sorted(self.active.values(), key=lambda a: -PRIORITY[a.priority])

    def has_active(self, min_priority: str = "WARNING") -> bool:
        thresh = PRIORITY[min_priority]
        return any(PRIORITY[a.priority] >= thresh for a in self.active.values())


# ===========================================================================
# PLC engine
# ===========================================================================
class PLC:
    """
    Programmable logic controller for the three-tank cascade.

    I/O image tables:
      ai : analog inputs   - transmitter readings (engineering units)
      i  : digital inputs  - buttons, E-stop, run feedback
      aq : analog outputs  - pump speed / valve positions (0..100 %)
      q  : digital outputs - pump run coil, alarm horn/light, status light
      m  : internal markers (M bits)
    """

    STATE_IDLE = "IDLE"
    STATE_STARTING = "STARTING"
    STATE_RUNNING = "RUNNING"
    STATE_STOPPING = "STOPPING"
    STATE_FAULTED = "FAULTED"
    STATE_E_STOPPED = "E_STOPPED"

    # Fail-safe actuator positions (%).  This water cascade isolates the
    # train: pump stopped, all transfer valves closed.
    FAILSAFE = {"P-101": 0.0, "XV-101": 0.0, "XV-102": 0.0, "XV-103": 0.0}

    def __init__(self) -> None:
        # --- I/O image tables -------------------------------------------
        self.ai: dict[str, float] = {f"LT-{n}": 0.0 for n in (101, 102, 103)}
        self.ai.update({f"XV-{n}_FB": 0.0 for n in (101, 102, 103)})
        self.i: dict[str, bool] = {
            "I0.0_ESTOP": False,
            "I0.1_START": False,
            "I0.2_STOP": False,
            "I0.3_RESET": False,
            "I0.4_PUMP_FB": False,
        }
        self.aq: dict[str, float] = {t: 0.0 for t in ("P-101", "XV-101", "XV-102", "XV-103")}
        self.q: dict[str, bool] = {"Q0.0_PUMP": False, "Q0.1_HORN": False,
                                   "Q0.2_LIGHT": False, "Q0.3_STATUS": True}
        self.m: dict[str, bool] = {}

        # --- Operator setpoints held in data registers ------------------
        self.loop_mode = {"LIC-101": "AUTO", "LIC-102": "AUTO"}
        self.loop_manual_mv = {"LIC-101": 0.0, "LIC-102": 0.0}
        self.manual_valves = {"XV-102": 50.0, "XV-103": 50.0}
        self.manual_pump = 0.0
        # Cascade outer -> inner setpoint (computed each scan when enabled).
        self.cascade_inner_sp: float | None = None

        # --- PID controllers --------------------------------------------
        self.pids: dict[str, PIDController] = {}
        for tag, spec in config.PID_LOOPS.items():
            self.pids[tag] = PIDController(
                tag=tag, kp=spec["kp"], ki=spec["ki"], kd=spec["kd"],
                mv_min=spec["mv_min"], mv_max=spec["mv_max"],
                ts=config.PLC_SCAN_DT, setpoint=spec["setpoint"],
                direct=spec["direct"], deriv_filter=spec["deriv_filter"],
                manual_output=spec.get("init_mv", 0.0),
            )

        # --- Control state ----------------------------------------------
        self.state = self.STATE_IDLE
        self.t = 0.0
        self.first_scan = True
        self.scan_count = 0
        self.alarms = AlarmManager()

        # Function block instances.
        self._start_timer = TON(1.0)
        self._stop_timer = TON(1.0)
        self._pump_trip_timer = TON(2.0)      # run-feedback watchdog
        self._valve_fault_timer = TON(2.0)    # travel-deviation watchdog
        self._pump_cycle_ctr = CTU(100000)    # maintenance counter (demo)
        self._trig_start = RTrig()
        self._trig_stop = RTrig()
        self._trig_reset = RTrig()
        self._trip_latch = RS(False)          # pump trip latch
        self._valve_fault_latch = RS(False)   # valve travel fault latch
        self._sensor_fault_latch = {f"LT-{n}": RS(False) for n in (101, 102, 103)}

        # Sensor rate-of-change tracking.
        self._last_level = {f"LT-{n}": 0.0 for n in (101, 102, 103)}

    # ------------------------------------------------------------------
    # Operator command interface (writes into the input image / memory)
    # ------------------------------------------------------------------
    def pulse_start(self) -> None:
        self.i["I0.1_START"] = True

    def pulse_stop(self) -> None:
        self.i["I0.2_STOP"] = True

    def pulse_reset(self) -> None:
        self.i["I0.3_RESET"] = True

    def set_estop(self, active: bool) -> None:
        self.i["I0.0_ESTOP"] = bool(active)

    def set_pump_feedback(self, running: bool) -> None:
        self.i["I0.4_PUMP_FB"] = bool(running)

    def set_valve_feedback(self, tag: str, position_pct: float) -> None:
        self.ai[f"{tag}_FB"] = float(position_pct)

    def acknowledge(self, tag: str) -> bool:
        return self.alarms.acknowledge(tag, self.t)

    def acknowledge_all(self) -> int:
        return self.alarms.acknowledge_all(self.t)

    def set_setpoint(self, tag: str, sp: float) -> None:
        if tag in self.pids:
            self.pids[tag].setpoint = float(sp)

    def set_tuning(self, tag: str, kp: float | None = None,
                   ki: float | None = None, kd: float | None = None) -> None:
        if tag in self.pids:
            self.pids[tag].set_tuning(kp=kp, ki=ki, kd=kd)

    def set_loop_mode(self, tag: str, mode: str, manual_mv: float | None = None) -> None:
        if tag not in self.pids:
            return
        mode = mode.upper()
        if mode == "MANUAL" and manual_mv is not None:
            self.loop_manual_mv[tag] = max(0.0, min(100.0, float(manual_mv)))
        self.loop_mode[tag] = mode

    def set_manual_valve(self, tag: str, position_pct: float) -> None:
        if tag in self.manual_valves:
            self.manual_valves[tag] = max(0.0, min(100.0, float(position_pct)))

    def set_manual_pump(self, speed_pct: float) -> None:
        self.manual_pump = max(0.0, min(100.0, float(speed_pct)))

    # ------------------------------------------------------------------
    # Scan cycle
    # ------------------------------------------------------------------
    def scan(self, dt: float) -> None:
        self.scan_count += 1
        self.t += dt

        # INPUT SCAN: edge-detect button pulses latched in the image table.
        start_edge = self._trig_start.update(self.i["I0.1_START"])
        stop_edge = self._trig_stop.update(self.i["I0.2_STOP"])
        reset_edge = self._trig_reset.update(self.i["I0.3_RESET"])
        estop = self.i["I0.0_ESTOP"]

        # PROGRAM SCAN: execute networks in order.
        self._network_sensor_plausibility(dt)
        self._network_level_alarms()
        self._network_state_machine(start_edge, stop_edge, reset_edge, estop)
        self._network_control_loops(dt)
        self._network_interlocks_and_outputs(dt)

        # OUTPUT UPDATE: alarm coil mapping.
        self.q["Q0.1_HORN"] = self.alarms.has_active("HIGH")
        self.q["Q0.2_LIGHT"] = self.alarms.has_active("WARNING")
        self.q["Q0.3_STATUS"] = not self.q["Q0.1_HORN"]

        # Clear single-scan button pulses.
        self.i["I0.1_START"] = False
        self.i["I0.2_STOP"] = False
        self.i["I0.3_RESET"] = False

        self.first_scan = False

    # ------------------------------------------------------------------
    def _network_sensor_plausibility(self, dt: float) -> None:
        """Network: detect failed level transmitters (NaN or implausible slew)."""
        for n in (101, 102, 103):
            tag = f"LT-{n}"
            val = self.ai.get(tag, 0.0)
            prev = self._last_level[tag]
            bad = math.isnan(val) or math.isinf(val)
            if not bad and self.scan_count > 1:
                rate = abs(val - prev) / dt if dt > 0 else 0.0
                bad = rate > config.SENSOR_RATE_LIMIT
            self._last_level[tag] = val

            latch = self._sensor_fault_latch[tag]
            if bad:
                latch.update(True, False)      # latch until operator reset
            if latch.q:
                self.alarms.raise_alarm(f"{tag}_FAULT",
                                        f"{tag} transmitter fault (plausibility)",
                                        "CRITICAL", self.t)
            else:
                self.alarms.clear_alarm(f"{tag}_FAULT", self.t)

    def _network_level_alarms(self) -> None:
        """Network: HIHI / HI / LO level alarms from the input image."""
        for n in (101, 102, 103):
            tank = f"TK-{n}"
            lt = f"LT-{n}"
            val = self.ai.get(lt, 0.0)
            if math.isnan(val):
                continue
            frac = val / config.TANK_HEIGHT
            if frac >= config.LEVEL_HIHI:
                self.alarms.raise_alarm(f"LSHH-{n}", f"{tank} HIGH-HIGH level",
                                        "CRITICAL", self.t)
            else:
                self.alarms.clear_alarm(f"LSHH-{n}", self.t)
            if frac >= config.LEVEL_HI:
                self.alarms.raise_alarm(f"LSH-{n}", f"{tank} HIGH level", "HIGH", self.t)
            else:
                self.alarms.clear_alarm(f"LSH-{n}", self.t)
            if frac <= config.LEVEL_LO:
                self.alarms.raise_alarm(f"LSL-{n}", f"{tank} LOW level", "WARNING", self.t)
            else:
                self.alarms.clear_alarm(f"LSL-{n}", self.t)

    def _network_state_machine(self, start_edge, stop_edge, reset_edge, estop) -> None:
        """Network: ISA-88 / PackML operating state machine."""

        # E-stop is always the highest authority.
        if estop:
            if self.state != self.STATE_E_STOPPED:
                self.alarms.raise_alarm("ESTOP", "Emergency stop active",
                                        "CRITICAL", self.t, auto_ack=True)
            self.state = self.STATE_E_STOPPED
            return

        state = self.state

        # Reset latches and return to IDLE (only valid from a tripped state).
        if reset_edge and state in (self.STATE_FAULTED, self.STATE_E_STOPPED):
            self._trip_latch.update(False, True)
            self._valve_fault_latch.update(False, True)
            for latch in self._sensor_fault_latch.values():
                latch.update(False, True)
            self.alarms.clear_alarm("ESTOP", self.t)
            self.alarms.clear_alarm("P-101_TRIP", self.t)
            self.alarms.clear_alarm("VALVE_FAULT", self.t)
            self.state = self.STATE_IDLE
            return

        # Hard interlocks take the process to a safe (faulted) state.
        if state == self.STATE_RUNNING and (self._trip_latch.q or self._valve_fault_latch.q):
            self.state = self.STATE_FAULTED
            return

        if state == self.STATE_IDLE:
            permitted = not self._trip_latch.q and not self._valve_fault_latch.q
            if start_edge and permitted:
                self._start_timer.reset()
                self.state = self.STATE_STARTING
        elif state == self.STATE_STARTING:
            if self._start_timer.update(True, config.PLC_SCAN_DT):
                self.state = self.STATE_RUNNING
        elif state == self.STATE_RUNNING:
            if stop_edge:
                self._stop_timer.reset()
                self.state = self.STATE_STOPPING
        elif state == self.STATE_STOPPING:
            if self._stop_timer.update(True, config.PLC_SCAN_DT):
                self.state = self.STATE_IDLE
        elif state == self.STATE_FAULTED:
            if reset_edge:
                self._trip_latch.update(False, True)
                self._valve_fault_latch.update(False, True)
                for latch in self._sensor_fault_latch.values():
                    latch.update(False, True)
                self.state = self.STATE_IDLE

    def _network_control_loops(self, dt: float) -> None:
        """Network: run the PID blocks (meaningful only while running).

        With cascade enabled (config.CASCADE_ENABLED), the outer loop
        (LIC-102) runs first and its output positions the inner loop's
        (LIC-101) setpoint within a configured range; otherwise each loop
        tracks its operator setpoint independently.
        """
        running = self.state in (self.STATE_STARTING, self.STATE_RUNNING)
        if config.CASCADE_ENABLED:
            self._run_loop("LIC-102", dt, running)
            outer_mv = max(0.0, min(100.0, self.pids["LIC-102"].mv))
            self.cascade_inner_sp = (
                config.CASCADE_INNER_SP_MIN
                + (outer_mv / 100.0)
                * (config.CASCADE_INNER_SP_MAX - config.CASCADE_INNER_SP_MIN))
            self._run_loop("LIC-101", dt, running)
        else:
            self.cascade_inner_sp = None
            for tag in self.pids:
                self._run_loop(tag, dt, running)

    def _effective_setpoint(self, tag: str) -> float:
        """The setpoint the loop actually controls this scan.  In cascade,
        LIC-101's setpoint is positioned by the outer loop; other loops keep
        their operator setpoint."""
        if (config.CASCADE_ENABLED and tag == "LIC-101"
                and self.cascade_inner_sp is not None):
            return self.cascade_inner_sp
        return self.pids[tag].setpoint

    def _schedule_gains(self, tag: str) -> None:
        """Zone-based gain scheduling for LIC-101 (opt-in): gains step up in
        the low and high level zones for faster recovery / overflow
        protection, while the nominal zone keeps the tuned gains."""
        if not config.GAIN_SCHEDULING_ENABLED or tag != "LIC-101":
            return
        pv = self.ai.get("LT-101", 0.0)
        frac = pv / config.TANK_HEIGHT
        kp, ki = config.GAIN_SCHEDULE[0][1], config.GAIN_SCHEDULE[0][2]
        for lo, gkp, gki in config.GAIN_SCHEDULE:
            if frac >= lo:
                kp, ki = gkp, gki
        self.pids[tag].set_tuning(kp=kp, ki=ki)

    def _run_loop(self, tag: str, dt: float, running: bool) -> None:
        """Advance one PID block for a scan: sensor-fault fail-safe, mode,
        optional gain scheduling and cascade setpoint positioning."""
        pid = self.pids[tag]
        lt = config.PID_LOOPS[tag]["pv_tag"]
        pv = self.ai.get(lt, 0.0)
        sensor_fault = self._sensor_fault_latch[lt].q

        # Fail the loop to a safe manual output on a bad measurement.
        if math.isnan(pv) or sensor_fault:
            pid.set_mode("MANUAL", 0.0)
            self.loop_mode[tag] = "MANUAL"
            self.loop_manual_mv[tag] = 0.0
            pid.update(pv if not math.isnan(pv) else 0.0, manual_mv=0.0)
            return

        self._schedule_gains(tag)
        pid.set_mode(self.loop_mode[tag], self.loop_manual_mv[tag])
        if running:
            pid.update(pv, manual_mv=self.loop_manual_mv[tag],
                       setpoint=self._effective_setpoint(tag))
        else:
            # Hold MV at the fail-safe value; keep PV fresh for display.
            pid.pv = pv
            pid.mv = 0.0
            pid._integral = 0.0
            pid.p_term = pid.i_term = pid.d_term = 0.0

    def _network_interlocks_and_outputs(self, dt: float) -> None:
        """Network: map control outputs to the output image, apply interlocks."""
        running = self.state in (self.STATE_STARTING, self.STATE_RUNNING)

        pump_cmd = self.pids["LIC-101"].mv
        v1_cmd = self.pids["LIC-102"].mv
        v2_cmd = self.manual_valves["XV-102"]
        v3_cmd = self.manual_valves["XV-103"]

        if self.loop_mode["LIC-101"] == "MANUAL":
            pump_cmd = self.manual_pump

        if not running:
            pump_cmd = v1_cmd = v2_cmd = v3_cmd = 0.0

        # Interlocks (highest authority wins).
        estop = self.i["I0.0_ESTOP"]
        h1 = self.ai.get("LT-101", 0.0) / config.TANK_HEIGHT
        h2 = self.ai.get("LT-102", 0.0) / config.TANK_HEIGHT
        h3 = self.ai.get("LT-103", 0.0) / config.TANK_HEIGHT

        if estop:
            pump_cmd = v1_cmd = v2_cmd = v3_cmd = 0.0
        if h1 >= config.LEVEL_HIHI:
            pump_cmd = 0.0
        if h2 >= config.LEVEL_HIHI:
            v1_cmd = 0.0
        if h3 >= config.LEVEL_HIHI:
            v2_cmd = 0.0
        if self._trip_latch.q:
            pump_cmd = 0.0
        if self._valve_fault_latch.q:
            v1_cmd = v2_cmd = v3_cmd = 0.0

        self.aq["P-101"] = max(0.0, min(100.0, pump_cmd))
        self.aq["XV-101"] = max(0.0, min(100.0, v1_cmd))
        self.aq["XV-102"] = max(0.0, min(100.0, v2_cmd))
        self.aq["XV-103"] = max(0.0, min(100.0, v3_cmd))
        self.q["Q0.0_PUMP"] = self.aq["P-101"] > 0.5

        # Pump run-feedback watchdog: commanded ON but no flow switch.
        no_feedback = self.q["Q0.0_PUMP"] and not self.i["I0.4_PUMP_FB"]
        if self._pump_trip_timer.update(no_feedback, dt):
            self._trip_latch.update(True, False)
            self.alarms.raise_alarm("P-101_TRIP", "P-101 pump trip (no run feedback)",
                                    "CRITICAL", self.t)

        # Valve travel watchdog: commanded vs. position feedback deviation.
        valve_dev = False
        for vtag in ("XV-101", "XV-102", "XV-103"):
            fb = self.ai.get(f"{vtag}_FB", self.aq[vtag])
            valve_dev = valve_dev or abs(self.aq[vtag] - fb) > 15.0
        if self._valve_fault_timer.update(valve_dev, dt):
            self._valve_fault_latch.update(True, False)
            self.alarms.raise_alarm("VALVE_FAULT", "Motorised valve travel fault",
                                    "HIGH", self.t)

        # Maintenance counter (counts pump start cycles).
        self._pump_cycle_ctr.update(self.q["Q0.0_PUMP"])

    # ------------------------------------------------------------------
    # HMI export
    # ------------------------------------------------------------------
    def export(self) -> dict:
        pids = {}
        for tag, pid in self.pids.items():
            pids[tag] = {
                "sp": pid.setpoint, "sp_eff": self._effective_setpoint(tag),
                "pv": pid.pv, "mv": pid.mv,
                "mode": pid.mode, "kp": pid.kp, "ki": pid.ki, "kd": pid.kd,
                "p": pid.p_term, "i": pid.i_term, "d": pid.d_term,
                "saturated": pid.saturated, "error": pid.error,
            }
        return {
            "state": self.state,
            "t": self.t,
            "scan_count": self.scan_count,
            "ai": dict(self.ai),
            "i": dict(self.i),
            "aq": dict(self.aq),
            "q": dict(self.q),
            "interlocks": {
                "estop": self.i["I0.0_ESTOP"],
                "pump_trip": self._trip_latch.q,
                "valve_fault": self._valve_fault_latch.q,
                "hihi": {f"TK-{n}": (self.ai.get(f"LT-{n}", 0.0) / config.TANK_HEIGHT) >= config.LEVEL_HIHI
                         for n in (101, 102, 103)},
                "sensor_fault": {f"LT-{n}": self._sensor_fault_latch[f"LT-{n}"].q
                                 for n in (101, 102, 103)},
            },
            "loop_mode": dict(self.loop_mode),
            "manual_valves": dict(self.manual_valves),
            "manual_pump": self.manual_pump,
            "pids": pids,
            "alarms": [self._alarm_dict(a) for a in self.alarms.active_alarms()],
            "alarm_history": [self._alarm_dict(a) for a in self.alarms.history[-50:]],
        }

    @staticmethod
    def _alarm_dict(a: Alarm) -> dict:
        return {
            "tag": a.tag, "message": a.message, "priority": a.priority,
            "state": a.state, "t_raise": a.t_raise, "t_ack": a.t_ack,
            "t_clear": a.t_clear,
        }
