"""
Discrete PID controller (parallel / ISA form).

    u(t) = Kp * e(t) + Ki * integral(e) + Kd * d(e)/dt

Engineering features implemented (and why):

  * **Derivative on measurement** - the derivative term acts on -d(PV)/dt
    instead of d(e)/dt, so a step change in setpoint does not produce a
    "derivative kick" on the actuator.  A first-order filter (time constant
    `deriv_filter`) is applied to the derivative channel to reject
    measurement noise.

  * **Integral anti-windup** - when the output saturates, the integrator is
    back-calculated toward the saturated output with tracking time constant
    `Tt`, preventing integral wind-up during actuator saturation.

  * **Bumpless manual/auto transfer** - on switching from MANUAL to AUTO the
    integrator is re-initialised so the controller output equals the last
    manual output (no step on the actuator).

  * **Output limits** - MV is clamped to [mv_min, mv_max] every cycle.

  * **Direction** - `direct=True` increases MV for positive error (e.g. a
    level controller that opens a feed valve when level is low); `direct=False`
    is the reverse-acting convention.

The controller is sampled at the PLC scan rate; its internal sample time is
configurable so that a change of scan time does not silently change the
integral gain.

Discretisation: forward-Euler integral, backward-Euler filtered derivative.
"""

from __future__ import annotations


class PIDController:
    def __init__(self, tag: str, kp: float, ki: float, kd: float,
                 mv_min: float, mv_max: float, ts: float,
                 setpoint: float, direct: bool = True,
                 deriv_filter: float = 0.1, tracking_tc: float = 2.0,
                 manual_output: float | None = None) -> None:
        self.tag = tag
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.mv_min = float(mv_min)
        self.mv_max = float(mv_max)
        self.ts = float(ts)
        self.setpoint = float(setpoint)
        self.direct = bool(direct)
        self.deriv_filter = max(float(deriv_filter), 1e-6)
        self.tracking_tc = max(float(tracking_tc), 1e-6)

        self.mode = "AUTO"          # AUTO | MANUAL
        self.pv = 0.0
        self.mv = manual_output if manual_output is not None else 0.0
        self.error = 0.0
        self._integral = self.mv      # bumpless start: I starts at current MV
        self._last_pv = None          # set on first update
        self._deriv = 0.0

        # Exposed for SCADA display
        self.p_term = 0.0
        self.i_term = 0.0
        self.d_term = 0.0
        self.saturated = False

    # ------------------------------------------------------------------
    def _sign(self) -> float:
        return 1.0 if self.direct else -1.0

    def update(self, pv: float, manual_mv: float | None = None) -> float:
        """Advance one sample.  `manual_mv` (0..100) is used in MANUAL mode."""
        if self._last_pv is None:
            self._last_pv = float(pv)

        if self.mode == "MANUAL":
            self.pv = float(pv)
            if manual_mv is not None:
                self.mv = max(self.mv_min, min(self.mv_max, float(manual_mv)))
            # Track the process so an AUTO re-entry is bumpless.
            self._last_pv = float(pv)
            self.error = self.setpoint - self.pv
            self._integral = self.mv - self._proportional()
            self._deriv = 0.0
            self.p_term = self._proportional()
            self.i_term = 0.0
            self.d_term = 0.0
            self.saturated = False
            return self.mv

        self.pv = float(pv)
        e = self.setpoint - self.pv
        self.error = e

        # Proportional
        self.p_term = self._sign() * self.kp * e

        # Derivative on measurement (filtered).
        d_pv = (self.pv - self._last_pv) / self.ts
        # Backward-Euler first-order low-pass: y += (x - y) * ts/(Tf + ts)
        alpha = self.ts / (self.deriv_filter + self.ts)
        self._deriv += alpha * (d_pv - self._deriv)
        self.d_term = self._sign() * self.kd * (-self._deriv)

        # Integrate (forward Euler).
        i_raw = self._sign() * self.ki * e
        self._integral += i_raw * self.ts

        # Unsaturated output
        u_unsat = self.p_term + self._integral + self.d_term

        # Clamp and back-calculate the integrator (anti-windup).
        u = max(self.mv_min, min(self.mv_max, u_unsat))
        self.saturated = (u != u_unsat)
        if self.saturated:
            # Back-calculation: pull the integrator toward the clamped output.
            self._integral += (u - u_unsat) * self.ts / self.tracking_tc

        self.mv = u
        self.i_term = self._integral
        self._last_pv = self.pv
        return self.mv

    def _proportional(self) -> float:
        return self._sign() * self.kp * (self.setpoint - self.pv)

    # ------------------------------------------------------------------
    # Operator interface
    # ------------------------------------------------------------------
    def set_tuning(self, kp: float | None = None, ki: float | None = None,
                   kd: float | None = None) -> None:
        if kp is not None:
            self.kp = float(kp)
        if ki is not None:
            self.ki = float(ki)
        if kd is not None:
            self.kd = float(kd)

    def set_limits(self, mv_min: float | None = None, mv_max: float | None = None) -> None:
        if mv_min is not None:
            self.mv_min = float(mv_min)
        if mv_max is not None:
            self.mv_max = float(mv_max)
        self.mv = max(self.mv_min, min(self.mv_max, self.mv))

    def set_mode(self, mode: str, manual_mv: float | None = None) -> None:
        mode = mode.upper()
        if mode not in ("AUTO", "MANUAL"):
            raise ValueError(f"invalid mode {mode!r}")
        self.mode = mode
        if mode == "MANUAL" and manual_mv is not None:
            self.mv = max(self.mv_min, min(self.mv_max, float(manual_mv)))
            self._integral = self.mv

    def reset(self, mv: float = 0.0) -> None:
        """Re-initialise integrator so output starts at `mv`."""
        self.mv = max(self.mv_min, min(self.mv_max, float(mv)))
        self._integral = self.mv
        self._deriv = 0.0
        self._last_pv = None
        self.saturated = False
