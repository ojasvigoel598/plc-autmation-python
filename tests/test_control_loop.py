"""Closed-loop integration tests: setpoint tracking, disturbance rejection,
safety interlocks and recovery."""

import pytest

from scada import config
from scada.runtime import Runtime


def run(rt, seconds, assert_running=False):
    n = int(seconds / config.PLC_SCAN_DT)
    for _ in range(n):
        rt.step()
    if assert_running:
        assert rt.plc.state == "RUNNING"


@pytest.fixture
def settled():
    rt = Runtime()
    rt.plc.pulse_start()
    run(rt, 120.0)
    assert rt.plc.state == "RUNNING"
    return rt


def test_converges_to_setpoint(settled):
    rt = settled
    h2 = rt.plant.levels["TK-102"]
    assert abs(h2 - rt.plc.pids["LIC-102"].setpoint) < 0.02


def test_setpoint_step_tracking(settled):
    rt = settled
    rt.plc.set_setpoint("LIC-102", 1.0)
    run(rt, 90.0)
    assert abs(rt.plant.levels["TK-102"] - 1.0) < 0.03


def test_disturbance_rejection(settled):
    rt = settled
    sp = rt.plc.pids["LIC-102"].setpoint
    rt.inject_disturbance("TK-102", 0.004, 30.0)
    # During the disturbance the level dips but the controller compensates.
    run(rt, 90.0)
    assert abs(rt.plant.levels["TK-102"] - sp) < 0.03


def test_pump_trip_causes_safe_state_and_recovers(settled):
    rt = settled
    rt.trip_pump()
    run(rt, 5.0)   # watchdog (2 s) trips the interlock
    assert rt.plc.state == "FAULTED"
    assert rt.plc.q["Q0.0_PUMP"] is False
    assert any(a["tag"] == "P-101_TRIP" for a in rt.plc.export()["alarms"])
    # Operator resets the field fault and the PLC; process restarts.
    rt.reset_pump()
    rt.plc.pulse_reset()
    run(rt, 2.0)
    assert rt.plc.state == "IDLE"
    rt.plc.pulse_start()
    run(rt, 5.0)
    assert rt.plc.state == "RUNNING"


def test_valve_stuck_detected(settled):
    rt = settled
    rt.stick_valve("XV-102")
    # force a command deviation: operator moves the valve while it is stuck
    rt.plc.set_manual_valve("XV-102", 90.0)
    run(rt, 4.0)
    assert rt.plc._valve_fault_latch.q is True
    assert "VALVE_FAULT" in rt.plc.alarms.active


def test_no_sustained_oscillation(settled):
    rt = settled
    samples = []
    run(rt, 50.0)
    for _ in range(200):   # another 20 s of steady-state observation
        rt.step()
        samples.append(rt.plant.levels["TK-102"])
    assert max(samples) - min(samples) < 0.03   # no limit cycle


def test_snapshot_includes_interlocks(settled):
    rt = settled
    s = rt.snapshot()
    assert "interlocks" in s
    assert s["interlocks"]["estop"] is False
    assert s["interlocks"]["pump_trip"] is False
    assert s["interlocks"]["hihi"]["TK-101"] is False
    rt.plc.set_estop(True)
    rt.step()
    s2 = rt.snapshot()
    assert s2["interlocks"]["estop"] is True


def test_mass_balance_over_full_run():
    rt = Runtime()
    rt.plc.pulse_start()
    v0 = sum(t.area * rt.plant.levels[t.tag] for t in rt.plant.tanks.values())
    dt = config.PLC_SCAN_DT
    net = 0.0
    for _ in range(1500):
        rt.step(dt)
        net += (rt.plant.flows["P-101"] - rt.plant.flows["XV-103"]
                - rt.plant.flows["overflow"]) * dt
    v1 = sum(t.area * rt.plant.levels[t.tag] for t in rt.plant.tanks.values())
    assert abs((v1 - v0) - net) < 0.01
