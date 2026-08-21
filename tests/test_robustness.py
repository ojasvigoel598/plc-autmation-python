"""Out-of-sample robustness suite.

The in-sample suite (test_control_loop.py) tunes and validates the PIDs on
one fixed seed, one operating point and one disturbance magnitude — a
classic overfitting trap.  This suite is the out-of-sample backtest: it
perturbs the noise seed, the setpoint, the disturbance size, the plant
geometry and combinations of all of them, and asserts an honest performance
envelope (mean error < 0.05 m after settling).  It also pins the documented
reachability limit: with LIC-101 holding TK-101 at its 1.0 m setpoint, the
gravity head into TK-102 cannot sustain TK-102 setpoints above ~1.2 m, so
the controller saturates there instead of oscillating or overflowing.

All trials are deterministic (explicit seeds) so a failure is reproducible.
"""

import random

import pytest

from scada import config
from scada.runtime import Runtime
from scada.process import Plant

DT = config.PLC_SCAN_DT
# Honest envelope from empirical probing (in-sample errors ~0.011..0.019;
# worst robust case ~0.036).  Looser than the in-sample numbers on purpose.
SETTLE_TOL = 0.05


def run(rt, seconds):
    for _ in range(int(seconds / DT)):
        rt.step()


def _make(seed, kv_scale=1.0, area_scale=1.0, init_h2=0.8) -> Runtime:
    rt = Runtime()
    if kv_scale != 1.0:
        for v in rt.plant.valves.values():
            v.kv *= kv_scale
    if area_scale != 1.0:
        for t in rt.plant.tanks.values():
            t.area *= area_scale
    rt.plant.rng = __import__("numpy").random.default_rng(seed)
    rt.plant.levels["TK-102"] = init_h2
    rt.plc.pulse_start()
    return rt


def _steady_error(rt, window_s=20.0) -> float:
    """Max |TK-102 level - setpoint| over the last window of a settled run."""
    sp = rt.plc.pids["LIC-102"].setpoint
    worst = 0.0
    for _ in range(int(window_s / DT)):
        rt.step()
        worst = max(worst, abs(rt.plant.levels["TK-102"] - sp))
    return worst


def _step_then_disturbance(rt, sp, dist, settle_s=90.0, hold_s=90.0):
    rt.plc.set_setpoint("LIC-102", sp)
    run(rt, settle_s)
    step_err = _steady_error(rt)
    rt.inject_disturbance("TK-102", dist, 30.0)
    run(rt, hold_s)
    dist_err = _steady_error(rt)
    return step_err, dist_err


# ---------------------------------------------------------------------------
# 1. Noise-seed robustness (the in-sample suite uses one fixed seed)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [1, 42, 999, 12345, 7])
def test_noise_seed_robustness(seed):
    rt = _make(seed)
    run(rt, 120.0)
    step_err, dist_err = _step_then_disturbance(rt, 1.0, 0.004)
    assert step_err < SETTLE_TOL
    assert dist_err < SETTLE_TOL


# ---------------------------------------------------------------------------
# 2. Out-of-sample setpoints across the reachable region
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sp", [0.4, 0.6, 0.9, 1.0, 1.2])
def test_setpoint_range(sp):
    rt = _make(seed=5)
    run(rt, 120.0)
    step_err, dist_err = _step_then_disturbance(rt, sp, 0.004)
    assert step_err < SETTLE_TOL, f"SP {sp} not held: {step_err:.3f} m"
    assert dist_err < SETTLE_TOL


# ---------------------------------------------------------------------------
# 3. Larger disturbances
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("dist", [0.004, 0.008, 0.012, 0.016])
def test_disturbance_magnitude(dist):
    rt = _make(seed=5)
    run(rt, 120.0)
    step_err, dist_err = _step_then_disturbance(rt, 1.0, dist)
    assert step_err < SETTLE_TOL
    assert dist_err < SETTLE_TOL


# ---------------------------------------------------------------------------
# 4. Plant-parameter perturbation (gain robustness)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kv_scale", [0.7, 0.85, 1.15, 1.3])
def test_valve_conductance_perturbation(kv_scale):
    rt = _make(seed=5, kv_scale=kv_scale)
    run(rt, 120.0)
    step_err, dist_err = _step_then_disturbance(rt, 1.0, 0.004)
    assert step_err < SETTLE_TOL
    assert dist_err < SETTLE_TOL


@pytest.mark.parametrize("area_scale", [0.7, 1.3])
def test_tank_area_perturbation(area_scale):
    rt = _make(seed=5, area_scale=area_scale)
    run(rt, 120.0)
    step_err, dist_err = _step_then_disturbance(rt, 1.0, 0.004)
    assert step_err < SETTLE_TOL
    assert dist_err < SETTLE_TOL


# ---------------------------------------------------------------------------
# 5. Documented reachability limit: SP above ~1.2 m on TK-102 is NOT
#    achievable while LIC-101 holds TK-101 at 1.0 m.  The controller must
#    saturate cleanly (bounded level, no overflow), not oscillate.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sp", [1.4, 1.6])
def test_unreachable_setpoint_saturates_cleanly(sp):
    rt = _make(seed=5)
    run(rt, 120.0)
    rt.plc.set_setpoint("LIC-102", sp)
    run(rt, 120.0)
    level = rt.plant.levels["TK-102"]
    # The setpoint is NOT reached (documented physical/control constraint)...
    assert abs(level - sp) > 0.1
    # ...but the plant stays bounded: no overflow, no runaway.
    assert level <= config.TANK_HEIGHT
    assert rt.plant.levels["TK-101"] <= config.TANK_HEIGHT
    # Feed is saturated at the constraint, not hunting.
    assert rt.plc.aq["XV-101"] >= 99.0


# ---------------------------------------------------------------------------
# 6. Randomized out-of-sample fuzz: combined perturbations
# ---------------------------------------------------------------------------
def test_randomized_fuzz_trials():
    rng = random.Random(20240821)
    for i in range(12):
        seed = rng.randrange(1, 100000)
        sp = round(rng.uniform(0.4, 1.2), 2)
        dist = round(rng.uniform(0.002, 0.016), 4)
        kv = round(rng.uniform(0.8, 1.2), 3)
        area = round(rng.uniform(0.8, 1.2), 3)
        rt = _make(seed, kv_scale=kv, area_scale=area)
        run(rt, 120.0)
        step_err, dist_err = _step_then_disturbance(rt, sp, dist)
        assert step_err < 0.06, (
            f"trial {i}: seed={seed} sp={sp} dist={dist} kv={kv} "
            f"area={area} step_err={step_err:.3f}")
        assert dist_err < 0.06, (
            f"trial {i}: seed={seed} sp={sp} dist={dist} kv={kv} "
            f"area={area} dist_err={dist_err:.3f}")


# ---------------------------------------------------------------------------
# 7. Mass balance still closes under perturbations (no hidden leaks)
# ---------------------------------------------------------------------------
def test_mass_balance_under_perturbation():
    rt = _make(seed=9, kv_scale=1.2, area_scale=0.9)
    rt.plc.pulse_start()
    v0 = sum(t.area * rt.plant.levels[t.tag]
             for t in rt.plant.tanks.values())
    net = 0.0
    for _ in range(1500):
        rt.step(DT)
        net += (rt.plant.flows["P-101"] - rt.plant.flows["XV-103"]
                - rt.plant.flows["overflow"]) * DT
    v1 = sum(t.area * rt.plant.levels[t.tag]
             for t in rt.plant.tanks.values())
    assert abs((v1 - v0) - net) < 0.01
