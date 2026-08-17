"""PID controller unit tests."""

import pytest

from scada.pid import PIDController


def make(kp=2.0, ki=0.0, kd=0.0, direct=True, setpoint=10.0):
    return PIDController(tag="LIC-X", kp=kp, ki=ki, kd=kd,
                         mv_min=0.0, mv_max=100.0, ts=0.1,
                         setpoint=setpoint, direct=direct)


def test_proportional_only():
    pid = make(kp=2.0)
    mv = pid.update(8.0)   # error = +2 -> MV = 4
    assert mv == pytest.approx(4.0)


def test_reverse_acting():
    pid = make(kp=2.0, direct=False)
    mv = pid.update(8.0)   # error +2 -> reverse acting -> MV = -4 -> clamped to 0
    assert mv == pytest.approx(0.0)
    # with direct=False and PV above SP, MV goes positive
    pid2 = make(kp=2.0, direct=False)
    pid2.setpoint = 8.0
    mv2 = pid2.update(10.0)  # error = -2 -> +4
    assert mv2 == pytest.approx(4.0)


def test_output_limits_and_antiwindup():
    pid = make(kp=1.0, ki=10.0)
    pid.mv_min = 0.0
    pid.mv_max = 10.0
    for _ in range(2000):
        pid.update(0.0)   # error 10 -> saturates at 10
    assert pid.mv == pytest.approx(10.0)
    # Anti-windup keeps the integrator bounded (no runaway wind-up: without
    # it, the integral would reach ~20000 after 200 s of saturation).
    assert pid.i_term < 500.0


def test_derivative_on_measurement_has_no_setpoint_kick():
    pid = make(kp=2.0, kd=5.0)
    pid.update(10.0)
    pid.update(10.0)
    before = pid.mv
    pid.setpoint = 12.0
    after = pid.update(10.0)   # SP step, PV unchanged
    # Only the proportional term should respond (2 * 2 = 4); no derivative kick.
    assert (after - before) == pytest.approx(4.0, abs=0.05)


def test_bumpless_manual_to_auto_transfer():
    pid = make(kp=1.0, ki=1.0)
    pid.set_mode("MANUAL", 40.0)
    pid.update(10.0, manual_mv=40.0)
    assert pid.mv == pytest.approx(40.0)
    pid.set_mode("AUTO")
    mv = pid.update(10.0)   # e = 0 at transfer
    assert mv == pytest.approx(40.0, abs=1e-6)


def test_manual_mode_follows_manual_output():
    pid = make(kp=2.0)
    pid.set_mode("MANUAL")
    mv = pid.update(7.0, manual_mv=66.6)
    assert mv == pytest.approx(66.6)
