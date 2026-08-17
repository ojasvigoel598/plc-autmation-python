"""
Physical plant model: a three-tank gravity cascade.

The plant is deliberately free of control logic.  It accepts *effective*
actuator positions (pump speed, valve openings already clamped and
fault-adjusted by the PLC/runtime layer) and advances the tank levels by one
time step.  All conservation of mass is handled here.

Model equations
---------------
Each tank is an integrator of net volumetric flow:

    A_i * dh_i/dt = sum(Q_in) - sum(Q_out)

Valve flow follows the standard sharp-edged orifice equation:

    Q = u * Cd * Ao * sqrt(2 g dh)

where `dh` is the hydraulic head between the two nodes (including the tank
base elevations) and `u` in [0, 1] is the fractional opening.  Flow is only
positive when the upstream node is higher than the downstream node and the
upstream tank is non-empty.

Pump flow is a lightly drooping centrifugal characteristic:

    Q_pump = u * Q_max * max(0, 1 - DROOP * head)

Numerical method
----------------
The ODE is integrated with the classical fourth-order Runge-Kutta method
(RK4) at a fixed sub-step (PROCESS_DT).  Rationale:

  * The process time constants are tens of seconds (A / (dQ/dh) is large),
    so even explicit Euler at 10 Hz would be stable.  RK4 is chosen for
    *accuracy and robustness*, not stability: valve flows are sqrt(h)
    nonlinearities and disturbances are step-like, so a 4th-order method
    keeps the mass balance tight for negligible cost (4 derivative
    evaluations per sub-step).
  * A fixed step is kept (rather than scipy's adaptive RK45) for
    deterministic, reproducible behaviour and so that the process,
    controller and UI rates stay cleanly separated.

Tank overflow is modelled as overtopping: any level above the tank height is
spilled to drain and reported as overflow flow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import config


@dataclass
class Tank:
    tag: str
    area: float
    height: float
    z_base: float

    @property
    def capacity(self) -> float:
        """Usable volume in m^3."""
        return self.area * self.height


@dataclass
class Valve:
    tag: str
    kv: float
    upstream: str   # node name (tank tag or "reservoir"/"drain")
    downstream: str
    open_frac: float = 0.0      # actual (slew-limited) position
    blocked: bool = False       # physical blockage: flow forced to ~0
    slew_rate: float = 0.5      # max travel, fraction of full stroke per second
    target: float = 0.0         # commanded position

    def effective_open(self) -> float:
        return 0.0 if self.blocked else max(0.0, min(1.0, self.open_frac))


@dataclass
class Pump:
    tag: str
    max_flow: float
    droop: float
    speed: float = 0.0          # actual (slew-limited) speed, 0..1
    slew_rate: float = 0.5      # max ramp, fraction of full speed per second
    target: float = 0.0         # commanded speed

    def effective_speed(self) -> float:
        return max(0.0, min(1.0, self.speed))


class LevelTransmitter:
    """Analog level transmitter (0..tank height -> 0..100 %)."""

    def __init__(self, tag: str, tank_tag: str, noise_std: float = 0.0):
        self.tag = tag
        self.tank_tag = tank_tag
        self.noise_std = noise_std
        # Fault model
        self.fault = "OK"       # OK | STUCK | FAIL_HIGH | FAIL_LOW | DRIFT | NAN
        self._stuck_value = 0.0
        self._drift_value = 0.0

    def read(self, true_level: float, dt: float, rng: np.random.Generator) -> float:
        value = float(true_level)
        if self.fault == "STUCK":
            value = self._stuck_value
        elif self.fault == "FAIL_HIGH":
            value = float(config.TANK_HEIGHT)
        elif self.fault == "FAIL_LOW":
            value = 0.0
        elif self.fault == "DRIFT":
            self._drift_value += 0.01 * dt
            value = float(true_level) + self._drift_value
        elif self.fault == "NAN":
            return float("nan")

        if self.noise_std > 0.0:
            value += float(rng.normal(0.0, self.noise_std))

        # Normal transmitters read zero at empty and full scale at overflow.
        return max(0.0, min(config.TANK_HEIGHT, value))

    def set_fault(self, fault: str) -> None:
        self.fault = fault
        if fault == "STUCK":
            # frozen at the value observed on the next read; seed with current
            self._stuck_value = self._drift_value

    def inject_fault(self, fault: str, current_true_level: float) -> None:
        self.fault = fault
        self._drift_value = 0.0
        if fault == "STUCK":
            self._stuck_value = float(current_true_level)


class Plant:
    """Three-tank cascade.  Holds the physical state and integrates it."""

    def __init__(self) -> None:
        self.tanks = {tag: Tank(tag, **spec) for tag, spec in config.TANKS.items()}
        self.valves = {
            tag: Valve(tag, kv=spec["kv"], upstream=spec["upstream"],
                       downstream=spec["downstream"], open_frac=spec["init_open"],
                       target=spec["init_open"])
            for tag, spec in config.VALVES.items()
        }
        self.pumps = {
            "P-101": Pump("P-101", max_flow=config.PUMP_MAX_FLOW,
                          droop=config.PUMP_DROOP, speed=0.0, target=0.0)
        }

        self.levels = {tag: float(config.INITIAL_LEVELS[tag]) for tag in self.tanks}
        self.t = 0.0

        # Flow telemetry (m^3/s) recomputed every integration step.
        self.flows = {"P-101": 0.0, "XV-101": 0.0, "XV-102": 0.0, "XV-103": 0.0,
                      "overflow": 0.0}

        # Transmitters (field instrumentation).
        self.level_tx = {
            "LT-101": LevelTransmitter("LT-101", "TK-101", noise_std=0.001),
            "LT-102": LevelTransmitter("LT-102", "TK-102", noise_std=0.001),
            "LT-103": LevelTransmitter("LT-103", "TK-103", noise_std=0.001),
        }

        # Cumulative overflow volume (m^3) per tank, for alarm/mass-balance.
        self.overflow_volume = {tag: 0.0 for tag in self.tanks}

        # Leaks (process disturbances): uncontrolled outflow per tank (m^3/s).
        self.leaks = {tag: 0.0 for tag in self.tanks}

        self.rng = np.random.default_rng(1234)

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    def _head(self, tag: str) -> float:
        """Hydraulic head of a node relative to the drain datum."""
        if tag == "reservoir":
            return config.TANK_HEIGHT + 0.5  # large, effectively fixed head
        if tag == "drain":
            return 0.0
        tank = self.tanks[tag]
        return tank.z_base + self.levels[tag]

    def _valve_flow(self, valve: Valve, levels: dict[str, float]) -> float:
        u = valve.effective_open()
        if u <= 0.0:
            return 0.0
        up_h = self._head_of(levels, valve.upstream)
        down_h = self._head_of(levels, valve.downstream)
        # No outflow from an empty tank.
        if valve.upstream in self.tanks and levels[valve.upstream] <= 0.0:
            return 0.0
        dh = up_h - down_h
        if dh <= 0.0:
            return 0.0
        return u * valve.kv * math.sqrt(dh)

    def _head_of(self, levels: dict[str, float], node: str) -> float:
        if node == "reservoir":
            return config.TANK_HEIGHT + 0.5
        if node == "drain":
            return 0.0
        return self.tanks[node].z_base + levels[node]

    def _pump_flow(self, levels: dict[str, float]) -> float:
        pump = self.pumps["P-101"]
        u = pump.effective_speed()
        if u <= 0.0:
            return 0.0
        head = self._head_of(levels, "TK-101")
        droop = max(0.0, 1.0 - pump.droop * head)
        return u * pump.max_flow * droop

    def _flows(self, levels: dict[str, float]) -> dict[str, float]:
        """Compute all inter-node flows for a given level vector."""
        flows = {"XV-101": self._valve_flow(self.valves["XV-101"], levels),
                 "XV-102": self._valve_flow(self.valves["XV-102"], levels),
                 "XV-103": self._valve_flow(self.valves["XV-103"], levels)}
        flows["P-101"] = self._pump_flow(levels)
        return flows

    def _derivs(self, levels: dict[str, float]) -> dict[str, float]:
        """dh/dt for each tank."""
        flows = self._flows(levels)
        dh = {}
        for tag, tank in self.tanks.items():
            q_in = 0.0
            q_out = 0.0
            if tag == "TK-101":
                q_in += flows["P-101"]
            for vtag, valve in self.valves.items():
                if valve.downstream == tag:
                    q_in += flows[vtag]
                if valve.upstream == tag:
                    q_out += flows[vtag]
            # Uncontrolled leak acts as an extra outflow (only from a
            # non-empty tank).
            leak = self.leaks.get(tag, 0.0) if levels[tag] > 0.0 else 0.0
            dh[tag] = (q_in - q_out - leak) / tank.area
        return dh

    # ------------------------------------------------------------------
    # Actuator dynamics
    # ------------------------------------------------------------------
    def _advance_actuators(self, dt: float) -> None:
        """Move actual actuator positions toward their targets at the
        configured slew rate (models motorised valve / pump travel time)."""
        for valve in self.valves.values():
            err = valve.target - valve.open_frac
            step = valve.slew_rate * dt
            if abs(err) <= step:
                valve.open_frac = valve.target
            else:
                valve.open_frac += step if err > 0 else -step
            valve.open_frac = max(0.0, min(1.0, valve.open_frac))

        pump = self.pumps["P-101"]
        err = pump.target - pump.speed
        step = pump.slew_rate * dt
        if abs(err) <= step:
            pump.speed = pump.target
        else:
            pump.speed += step if err > 0 else -step
        pump.speed = max(0.0, min(1.0, pump.speed))

    # ------------------------------------------------------------------
    # Integration
    # ------------------------------------------------------------------
    def step(self, dt: float) -> None:
        """Advance the plant by `dt` seconds using RK4 sub-steps."""
        self._advance_actuators(dt)
        n = max(1, int(round(dt / config.PROCESS_DT)))
        h = dt / n
        levels = dict(self.levels)
        overflow_rate = 0.0

        for _ in range(n):
            k1 = self._derivs(levels)
            l2 = {t: levels[t] + 0.5 * h * k1[t] for t in levels}
            k2 = self._derivs(l2)
            l3 = {t: levels[t] + 0.5 * h * k2[t] for t in levels}
            k3 = self._derivs(l3)
            l4 = {t: levels[t] + h * k3[t] for t in levels}
            k4 = self._derivs(l4)

            for tag in levels:
                levels[tag] += (h / 6.0) * (k1[tag] + 2 * k2[tag] + 2 * k3[tag] + k4[tag])

            # Clamp to physical limits; excess volume becomes overtopping.
            for tag, tank in self.tanks.items():
                if levels[tag] > tank.height:
                    excess = levels[tag] - tank.height
                    self.overflow_volume[tag] += excess * tank.area
                    overflow_rate += excess * tank.area / h
                    levels[tag] = tank.height
                elif levels[tag] < 0.0:
                    levels[tag] = 0.0

        self.levels = levels
        self.t += dt
        # Recompute flows at the final state for telemetry.
        self.flows = self._flows(self.levels)
        self.flows["overflow"] = overflow_rate

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------
    def read_levels(self) -> dict[str, float]:
        """True (noise-free) levels from the process, in metres."""
        return dict(self.levels)

    def read_transmitters(self, dt: float) -> dict[str, float]:
        """Sampled transmitter values (with noise/faults), in metres."""
        out = {}
        for tag, tx in self.level_tx.items():
            out[tag] = tx.read(self.levels[tx.tank_tag], dt, self.rng)
        return out

    def set_actuators(self, pump_speed: float | None = None,
                      valve_open: dict[str, float] | None = None) -> None:
        """Set actuator *targets* computed by the control layer.
        (Actual positions slew toward these targets in step().)"""
        if pump_speed is not None:
            self.pumps["P-101"].target = max(0.0, min(1.0, pump_speed))
        if valve_open is not None:
            for tag, frac in valve_open.items():
                self.valves[tag].target = max(0.0, min(1.0, frac))
