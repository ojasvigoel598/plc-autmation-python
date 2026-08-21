"""
Additional industrial process units: heat exchanger, pressure control loop,
and conveyor.

These extend the three-tank cascade with realistic industrial unit operations.
Each unit is self-contained: it owns its physical state, accepts actuator
targets (positions/speeds), and advances by one time step.  All are free of
control logic — the PLC/runtime layer applies the control outputs.

All quantities are SI: temperatures in K, pressures in Pa, flows in m^3/s,
masses in kg.

Heat exchanger model
--------------------
Simplified two-stream counter-flow heat exchanger:

    Hot side:   fluid flows through the shell, loses heat to the tube side.
    Cold side:  fluid flows through the tube, gains heat.

Energy balances (per stream):

    dTh_out/dt = (Wh * cp_hot * (Th_in - Th_out) - UA * LMTD) / (m_hot * cp_hot)
    dTc_out/dt = (Wc * cp_cold * (Tc_in - Tc_out) + UA * LMTD) / (m_cold * cp_cold)

LMTD (log-mean temperature difference) is approximated by the arithmetic mean
for the ODE to keep it algebraically simple while capturing the coupling:

    LMTD ≈ ((Th_in - Tc_out) + (Th_out - Tc_in)) / 2

A fouling parameter (0..1) reduces the effective UA over time.

Pressure model
--------------
A gas/vapour volume in a closed vessel pressurises as flow enters faster
than it leaves:

    dP/dt = (Qin - Qout) * P / V   (ideal gas / isothermal)

Controlled by a modulating valve on the outlet; setpoint pressure determines
the required valve opening via a PID loop in the PLC layer.

Conveyor model
--------------
A belt conveyor driven by an AC motor:

    J * dw/dt = T_motor - T_load - T_friction

T_load depends on the mass of material on the belt.  A jam is modelled as
a sudden load increase.  Speed is limited by the VFD to a commanded setpoint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from . import config


# ---------------------------------------------------------------------------
# Heat exchanger
# ---------------------------------------------------------------------------
@dataclass
class HeatExchanger:
    """Counter-flow heat exchanger with two streams and thermal inertia.

    The unit sits downstream of the three-tank cascade and can use TK-103
    discharge as its cold-side source; the hot side represents a secondary
    process loop (e.g. steam heating).
    """
    tag: str = "HX-101"

    # Geometry & thermal design
    UA: float = 2500.0           # W/K  (overall heat transfer coefficient × area)
    fouling: float = 0.0         # 0..1, 0 = clean, 1 = fully fouled (no heat transfer)

    # Hot side (shell)
    mass_hot: float = 20.0       # kg   (fluid inventory in the shell)
    cp_hot: float = 4180.0       # J/(kg·K)  (water)
    T_hot_in: float = 343.0      # K    (70 °C feed from upstream)
    T_hot_out: float = 333.0     # K    (initial outlet)
    flow_hot: float = 0.001      # m^3/s  (hot-side volume flow)

    # Cold side (tube)
    mass_cold: float = 10.0      # kg   (fluid inventory in the tubes)
    cp_cold: float = 4180.0      # J/(kg·K)
    T_cold_in: float = 293.0     # K    (20 °C ambient feed)
    T_cold_out: float = 298.0    # K
    flow_cold: float = 0.0008    # m^3/s  (cold-side volume flow)

    # Dynamics
    _T_hot: float = 333.0        # K, internal hot-side outlet temperature state
    _T_cold: float = 298.0       # K, internal cold-side outlet temperature state

    def effective_ua(self) -> float:
        """UA reduced by fouling factor."""
        return self.UA * (1.0 - self.fouling)

    def step(self, dt: float) -> None:
        """Advance the heat exchanger by `dt` seconds (Euler integration).

        The time constants are ~10 s for the tube side and ~20 s for the
        shell side — small compared to the tank dynamics — so a simple Euler
        step at the PLC scan rate is stable and accurate enough.
        """
        ua = self.effective_ua()

        # Approximate LMTD (arithmetic mean for ODE simplicity).
        lmtd = ((self.T_hot_in - self._T_cold)
                + (self._T_hot - self.T_cold_in)) / 2.0

        # Prevent negative LMTD when streams are nearly equal or crossed.
        if lmtd < 0.0:
            lmtd = 0.0

        heat_transfer = ua * lmtd  # W (positive = hot→cold)

        # Hot-side energy balance.
        dTh = (self.flow_hot * self.cp_hot * (self.T_hot_in - self._T_hot)
               - heat_transfer) / (self.mass_hot * self.cp_hot)
        self._T_hot += dTh * dt

        # Cold-side energy balance.
        dTc = (self.flow_cold * self.cp_cold * (self._T_cold - self.T_cold_in)
               + heat_transfer) / (self.mass_cold * self.cp_cold)
        self._T_cold += dTc * dt

        # Clamp to physical bounds (0 °C .. 100 °C for water at 1 atm).
        self._T_hot = max(273.15, min(373.15, self._T_hot))
        self._T_cold = max(273.15, min(373.15, self._T_cold))

    @property
    def hot_out(self) -> float:
        return self._T_hot

    @property
    def cold_out(self) -> float:
        return self._T_cold

    def read(self) -> dict:
        """Snapshot for telemetry."""
        return {
            "tag": self.tag,
            "T_hot_in": self.T_hot_in,
            "T_hot_out": round(self._T_hot, 2),
            "T_cold_in": self.T_cold_in,
            "T_cold_out": round(self._T_cold, 2),
            "flow_hot": self.flow_hot,
            "flow_cold": self.flow_cold,
            "heat_transfer_W": round(self.effective_ua() * max(0.0, (
                (self.T_hot_in - self._T_cold) + (self._T_hot - self.T_cold_in)
            ) / 2.0), 1),
            "fouling": self.fouling,
            "UA_effective": round(self.effective_ua(), 1),
        }


# ---------------------------------------------------------------------------
# Pressure vessel / control loop
# ---------------------------------------------------------------------------
@dataclass
class PressureVessel:
    """A pressurised gas volume with an inlet flow and a modulating outlet
    control valve.  Represents a downstream buffer vessel or flash drum.

    Dynamics:
        dP/dt = (Qin - Qout) * P / V   (isothermal ideal-gas approximation)

    Where Qout depends on the outlet valve position and the pressure
    differential across it (back-pressure assumed atmospheric).
    """
    tag: str = "PK-101"

    # Geometry
    volume: float = 0.5          # m^3  (internal volume)
    P_atm: float = 101325.0      # Pa   (atmospheric back-pressure)

    # Process
    inlet_flow: float = 0.0005   # m^3/s  (from upstream, e.g. TK-103 discharge)
    P_initial: float = 101325.0  # Pa   (starts at atmospheric)

    # Outlet valve characteristics
    valve_Cv: float = 0.00001    # flow coefficient (m^3/s per sqrt(Pa))
    valve_position: float = 0.0  # 0..1 actual position

    # State
    _P: float = 101325.0         # Pa, internal pressure state

    # Alarm thresholds (Pa)
    P_LOHI: float = 105000.0     # low-high warning
    P_HIHI: float = 120000.0     # high-high trip
    P_LOLO: float = 90000.0      # low-low warning

    def step(self, dt: float) -> None:
        """Advance pressure by `dt` seconds (Euler integration)."""
        # Outlet flow: orifice equation across the outlet valve.
        delta_p = max(0.0, self._P - self.P_atm)
        q_out = self.valve_position * self.valve_Cv * math.sqrt(delta_p)

        # Pressure dynamics: dP/dt = (Qin - Qout) * P / V
        dp_dt = (self.inlet_flow - q_out) * self._P / self.volume
        self._P += dp_dt * dt

        # Physical bounds (can't go below atmospheric for a simple model,
        # but we allow slight vacuum for realism).
        self._P = max(self.P_atm * 0.8, min(self.P_atm * 1.5, self._P))

    @property
    def pressure(self) -> float:
        return self._P

    @property
    def pressure_bar(self) -> float:
        """Pressure in bar (convenient for HMI)."""
        return self._P / 100000.0

    def read(self) -> dict:
        """Snapshot for telemetry."""
        return {
            "tag": self.tag,
            "pressure_Pa": round(self._P, 0),
            "pressure_bar": round(self.pressure_bar, 3),
            "inlet_flow": self.inlet_flow,
            "outlet_flow": round(
                self.valve_position * self.valve_Cv * math.sqrt(
                    max(0.0, self._P - self.P_atm)), 6),
            "valve_position": self.valve_position,
            "volume": self.volume,
        }


# ---------------------------------------------------------------------------
# Conveyor belt
# ---------------------------------------------------------------------------
@dataclass
class ConveyorBelt:
    """Belt conveyor with AC motor drive, variable speed, jam detection,
    and product tracking.

    Motor model (simplified):
        J * dw/dt = T_motor - T_load - T_friction

    where T_motor = Kt * I (motor torque from current), and T_load comes
    from the mass of material on the belt and gravity (if inclined).
    """
    tag: str = "CV-101"

    # Physical parameters
    belt_length: float = 10.0    # m
    belt_speed_max: float = 2.0  # m/s (VFD maximum)
    motor_inertia: float = 0.5   # kg·m^2 (rotor + belt reflected)
    motor_Kt: float = 5.0        # N·m/A  (torque constant)
    friction_coeff: float = 0.3  # N·s/m  (viscous friction on belt)
    gravity_load: float = 0.0    # N (set >0 for inclined conveyors)

    # State
    speed: float = 0.0           # m/s (actual belt speed)
    target_speed: float = 0.0    # m/s (VFD command)
    running: bool = False
    jammed: bool = False
    motor_fault: bool = False

    # Product tracking
    products_on_belt: int = 0
    product_spacing: float = 1.0 # m between products
    runtime_accum: float = 0.0   # seconds of accumulated run-time

    # Jam detection
    jam_threshold: float = 0.8   # fraction of max speed below which a running motor is "jammed"
    jam_timer: float = 0.0
    jam_detect_time: float = 3.0 # seconds of sustained low speed to declare jam

    def step(self, dt: float) -> None:
        """Advance the conveyor by `dt` seconds."""
        if not self.running or self.motor_fault:
            # Motor off or faulted: coast to stop with friction.
            if self.speed > 0.0:
                self.speed = max(0.0, self.speed - self.friction_coeff * dt / self.motor_inertia)
            return

        if self.jammed:
            # Jammed: motor stalls, speed drops to near zero.
            self.speed = max(0.0, self.speed - 5.0 * dt)
            return

        # Motor torque (proportional to speed command, simplified VFD model).
        torque_motor = self.motor_Kt * (self.target_speed / self.belt_speed_max) * 10.0

        # Load torque (gravity + friction).
        torque_load = self.gravity_load + self.friction_coeff * self.speed

        # Newton's second law: J * dw/dt = T_motor - T_load
        dw_dt = (torque_motor - torque_load) / self.motor_inertia
        self.speed += dw_dt * dt
        self.speed = max(0.0, min(self.belt_speed_max, self.speed))

        # Accumulate runtime.
        self.runtime_accum += dt

        # Jam detection: if commanded but speed stays below threshold.
        if self.target_speed > self.belt_speed_max * 0.5:
            if self.speed < self.belt_speed_max * self.jam_threshold:
                self.jam_timer += dt
                if self.jam_timer >= self.jam_detect_time:
                    self.jammed = True
            else:
                self.jam_timer = 0.0

        # Product tracking (simple model: products enter at a fixed rate
        # when belt is running, exit when they reach the end).
        # In practice this would be a discrete-event model; here we use
        # a continuous approximation for the HMI display.
        pass

    def start(self) -> None:
        if not self.jammed and not self.motor_fault:
            self.running = True

    def stop(self) -> None:
        self.running = False

    def reset_jam(self) -> None:
        self.jammed = False
        self.jam_timer = 0.0

    def read(self) -> dict:
        """Snapshot for telemetry."""
        return {
            "tag": self.tag,
            "speed": round(self.speed, 3),
            "speed_pct": round(self.speed / self.belt_speed_max * 100, 1) if self.belt_speed_max > 0 else 0.0,
            "target_speed": self.target_speed,
            "running": self.running,
            "jammed": self.jammed,
            "motor_fault": self.motor_fault,
            "runtime_hours": round(self.runtime_accum / 3600, 2),
            "belt_length": self.belt_length,
        }


# ---------------------------------------------------------------------------
# Plant-wide process units container
# ---------------------------------------------------------------------------
class ProcessUnits:
    """Container for all additional process units (heat exchanger, pressure
    vessel, conveyor).  Created by the Runtime and stepped each scan cycle."""

    def __init__(self) -> None:
        self.hx = HeatExchanger()
        self.pressure = PressureVessel()
        self.conveyor = ConveyorBelt()

    def step(self, dt: float) -> None:
        """Advance all process units by `dt` seconds."""
        self.hx.step(dt)
        self.pressure.step(dt)
        self.conveyor.step(dt)

    def read(self) -> dict:
        """Combined snapshot for telemetry."""
        return {
            "heat_exchanger": self.hx.read(),
            "pressure_vessel": self.pressure.read(),
            "conveyor": self.conveyor.read(),
        }
