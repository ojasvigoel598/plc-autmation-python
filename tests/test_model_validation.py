"""
Analytic model validation — the simulator against closed-form solutions.

These tests hold the process model to a higher standard than "it behaves":
each one derives the *exact* expected behaviour from first principles and
checks the simulation reproduces it within a numerical tolerance.  They are
the evidence behind the claim that the plant model is physically faithful —
the sort of validation a control paper would be expected to show.

Covered:
  * Torricelli draining: exact solution of A·dh/dt = −kv·√(z+h)
  * Steady-state orifice chain: flows satisfy Q = u·kv·√(Δh) simultaneously
  * Pump characteristic: Q = u·Qmax·(1 − droop·head)
  * First law for the heat exchanger: Q_hot = Q_cold at steady state
  * Pressure vessel: ideal-gas ODE + steady-state valve balance
  * RK4 convergence order (global error ≈ O(dt⁴))
  * Reservoir mass balance (finite source)
"""

import math

import pytest

from scada import config
from scada.process import Plant
from scada.process_units import HeatExchanger, PressureVessel


# ---------------------------------------------------------------------------
# Torricelli draining: closed-form solution
# ---------------------------------------------------------------------------
def test_torricelli_draining_matches_exact_solution():
    """A tank draining to a zero-head drain obeys
    A·dh/dt = −u·kv·√(z_base + h), whose exact solution is
    h(t) = (√(H0) − (u·kv/(2A))·t)² − z_base."""
    plant = Plant()
    # Use TK-103 → XV-103 → drain: the downstream head is exactly 0, which is
    # what the closed-form solution assumes.  Isolate the other tanks so no
    # flow enters TK-103.
    plant.levels = {"TK-101": 1.5, "TK-102": 0.5, "TK-103": 1.5}
    plant.reservoir_level = 0.0                    # pump starves
    plant.valves["XV-101"].open_frac = 0.0         # isolate TK-101
    plant.valves["XV-101"].target = 0.0
    plant.valves["XV-102"].open_frac = 0.0         # isolate TK-102
    plant.valves["XV-102"].target = 0.0
    plant.valves["XV-103"].open_frac = 0.8
    plant.valves["XV-103"].target = 0.8
    plant.valves["XV-103"].blocked = False

    tank = plant.tanks["TK-103"]
    kv = plant.valves["XV-103"].kv
    u = 0.8
    A = tank.area
    z = tank.z_base
    H0 = z + plant.levels["TK-103"]

    dt = config.PLC_SCAN_DT
    t = 0.0
    for _ in range(150):                            # 15 s of draining
        plant.step(dt)
        t += dt
        H = z + plant.levels["TK-103"]
        exact_H = (math.sqrt(H0) - (u * kv / (2.0 * A)) * t) ** 2
        assert H == pytest.approx(exact_H, abs=2e-3)


def test_torricelli_draining_to_empty():
    """The analytic solution predicts the tank empties at a finite time;
    the simulator must not go negative and must end at ~0."""
    plant = Plant()
    plant.levels = {"TK-101": 1.0, "TK-102": 0.5, "TK-103": 0.5}
    plant.reservoir_level = 0.0
    for tag in ("XV-101", "XV-102", "XV-103"):
        plant.valves[tag].open_frac = 1.0
        plant.valves[tag].target = 1.0

    t = 0.0
    while plant.levels["TK-103"] > 1e-4 and t < 120.0:
        plant.step(0.1)
        t += 0.1
    assert plant.levels["TK-103"] >= 0.0
    assert plant.levels["TK-103"] < 1e-3


# ---------------------------------------------------------------------------
# Steady-state orifice chain
# ---------------------------------------------------------------------------
def test_steady_state_satisfies_orifice_equations():
    """At steady state every node must balance, so the flows must satisfy
    Q = u·kv·√(Δh) for each valve AND mass must be conserved through the
    chain: Q_P101 = Q_XV101 = Q_XV102 = Q_XV103."""
    plant = Plant()
    plant.set_actuators(pump_speed=0.6,
                        valve_open={"XV-101": 0.6, "XV-102": 0.5,
                                    "XV-103": 0.4})
    # Run long enough to settle (time constants are tens of seconds).  The
    # reservoir is held at its level (a level-controlled suction vessel), so
    # the plant can reach a true steady state instead of slowly draining.
    for _ in range(40000):
        plant.step(config.PLC_SCAN_DT)
        plant.reservoir_level = 3.0

    f = plant.flows
    q = f["XV-103"]          # the terminal flow is the throughput
    # Every measured flow must equal the orifice equation at the actual head.
    for vtag in ("XV-101", "XV-102", "XV-103"):
        v = plant.valves[vtag]
        up_h = plant._head_of(plant.levels, v.upstream)
        down_h = plant._head_of(plant.levels, v.downstream)
        expected = v.effective_open() * v.kv * math.sqrt(max(0.0, up_h - down_h))
        assert f[vtag] == pytest.approx(expected, rel=1e-6)
    # Mass is conserved through the chain (small tolerance for transients).
    assert f["P-101"] == pytest.approx(q, rel=1e-3)
    assert f["XV-101"] == pytest.approx(q, rel=1e-3)
    assert f["XV-102"] == pytest.approx(q, rel=1e-3)


def test_pump_characteristic_matches_model():
    """Q = u·Qmax·max(0, 1 − droop·head) — check droop against head."""
    plant = Plant()
    plant.reservoir_level = 3.0
    pump = plant.pumps["P-101"]
    pump.speed = 0.8
    pump.target = 0.8
    plant.levels = {"TK-101": 1.2, "TK-102": 0.5, "TK-103": 0.5}
    head = plant._head_of(plant.levels, "TK-101")
    expected = 0.8 * config.PUMP_MAX_FLOW * max(0.0, 1.0 - config.PUMP_DROOP * head)
    assert plant._pump_flow(plant.levels) == pytest.approx(expected, rel=1e-9)
    # A taller head must reduce delivery (drooping characteristic).
    plant.levels["TK-101"] = 1.8
    assert plant._pump_flow(plant.levels) < expected


# ---------------------------------------------------------------------------
# Heat exchanger: first law at steady state
# ---------------------------------------------------------------------------
def test_heat_exchanger_energy_balance_at_steady_state():
    """At steady state the heat leaving the hot stream must equal the heat
    entering the cold stream (first law), and both must equal UA·LMTD using
    the model's true log-mean temperature difference."""
    hx = HeatExchanger()
    # Run with fixed flows until the temperatures converge.
    for _ in range(20000):
        hx.step(0.1)
    ua = hx.effective_ua()
    q = ua * hx._lmtd()
    q_hot = hx.flow_hot * hx.density_hot * hx.cp_hot * (hx.T_hot_in - hx._T_hot)
    q_cold = hx.flow_cold * hx.density_cold * hx.cp_cold * (hx._T_cold - hx.T_cold_in)
    # First law: the common transfer rate closes on both streams.
    assert q_hot == pytest.approx(q_cold, rel=1e-3)
    assert q == pytest.approx(q_hot, rel=1e-3)
    # Second law: heat flows hot→cold, so cold outlet stays below the hot
    # inlet and hot outlet stays above the cold inlet.
    assert hx.T_cold_in < hx._T_cold < hx.T_hot_in
    assert hx.T_cold_in < hx._T_hot < hx.T_hot_in


def test_heat_exchanger_fouling_reduces_transfer():
    hx = HeatExchanger()
    clean = hx.effective_ua()
    hx.fouling = 0.5
    assert hx.effective_ua() == pytest.approx(clean * 0.5)


# ---------------------------------------------------------------------------
# Pressure vessel: ideal-gas ODE
# ---------------------------------------------------------------------------
def test_pressure_vessel_steady_state_valve_balance():
    """At steady state q_out = q_in, so P satisfies
    P = P_atm + (q_in / (pos·Cv))²  (outlet: q_out = pos·Cv·√(P − P_atm))."""
    pv = PressureVessel()
    pv.valve_position = 0.5
    pv.inlet_flow = 0.0005
    for _ in range(20000):
        pv.step(0.1)
    expected_p = pv.P_atm + (pv.inlet_flow / (pv.valve_position * pv.valve_Cv)) ** 2
    assert pv.pressure == pytest.approx(expected_p, rel=1e-4)


def test_pressure_vessel_pressurises_with_inlet():
    """With the outlet closed, dP/dt = Q·P/V and P must rise monotonically."""
    pv = PressureVessel()
    pv.valve_position = 0.0
    p0 = pv.pressure
    for _ in range(100):
        pv.step(0.1)
    assert pv.pressure > p0


# ---------------------------------------------------------------------------
# RK4 convergence order
# ---------------------------------------------------------------------------
def test_rk4_global_error_scales_as_dt_power_four(monkeypatch):
    """Verify the plant integrator is genuinely 4th-order by comparing against
    a closed-form exponential trajectory.

    A tank fed only by the drooping pump obeys
        dH/dt = u·Qmax·(1 − droop·H)/A,
    a linear ODE whose exact solution is an exponential (NOT a polynomial).
    The tank-geometry parameters are shrunk (small area, strong droop) so the
    time constant is sub-second and RK4's truncation error is far above float
    precision; halving the step must shrink the error ~16× (2⁴).

    Note: the classic draining-tank (Torricelli) test is deliberately NOT
    used here — its exact solution is a quadratic polynomial in t, which RK4
    integrates *exactly* at any step size, so it cannot expose the method's
    order.
    """
    # Fast pump-fed tank: small area + strong droop -> tau ~ 0.8 s.
    monkeypatch.setattr(config, "TANKS", {
        "TK-101": {"area": 0.02, "height": 2.0, "z_base": 1.50},
        "TK-102": {"area": 0.60, "height": 2.0, "z_base": 1.00},
        "TK-103": {"area": 0.50, "height": 2.0, "z_base": 0.50},
    })
    monkeypatch.setattr(config, "PUMP_MAX_FLOW", 0.05)
    monkeypatch.setattr(config, "PUMP_DROOP", 0.50)

    def run(process_dt, t_end):
        monkeypatch.setattr(config, "PROCESS_DT", process_dt)
        plant = Plant()
        plant.levels = {"TK-101": 0.1, "TK-102": 0.5, "TK-103": 0.5}
        plant.reservoir_level = 3.0
        plant.pumps["P-101"].speed = 1.0
        plant.pumps["P-101"].target = 1.0
        for v in plant.valves.values():
            v.open_frac = 0.0
            v.target = 0.0
        steps = int(t_end / process_dt)
        for _ in range(steps):
            plant.step(process_dt)
            plant.reservoir_level = 3.0
        return plant.levels["TK-101"]

    A = 0.02
    droop = 0.50
    u = 1.0
    qmax = 0.05
    H0 = 1.5 + 0.1
    t_end = 1.6          # ~2 time constants: mid-transient, error measurable
    H_exact = (1.0 / droop
               - (1.0 / droop - H0) * math.exp(-u * qmax * droop * t_end / A))
    exact_level = H_exact - 1.5

    e1 = abs(run(0.05, t_end) - exact_level)
    e2 = abs(run(0.025, t_end) - exact_level)
    e3 = abs(run(0.0125, t_end) - exact_level)

    # Each halving must cut the error ~16x (2^4).  Allow generous slack to
    # stay robust across platforms, but the ratios must be far from 2 (Euler)
    # and far from 1 (no convergence at all).
    assert e1 / e2 > 8.0
    assert e1 / e2 < 40.0
    assert e2 / e3 > 8.0
    assert e2 / e3 < 40.0


# ---------------------------------------------------------------------------
# Reservoir mass balance (finite source)
# ---------------------------------------------------------------------------
def test_reservoir_drains_under_pump_draw():
    """With no makeup flow the reservoir is a finite source: pumping must
    lower it, and a dry reservoir must starve the pump (no negative flow)."""
    plant = Plant()
    plant.set_actuators(pump_speed=1.0,
                        valve_open={"XV-101": 1.0, "XV-102": 1.0,
                                    "XV-103": 1.0})
    initial = plant.reservoir_level
    for _ in range(2000):
        plant.step(0.1)
    assert plant.reservoir_level < initial
    assert plant.reservoir_level >= 0.0
    # Once the reservoir is empty the pump must deliver nothing.
    plant.reservoir_level = 0.0
    assert plant._pump_flow(plant.levels) == 0.0
