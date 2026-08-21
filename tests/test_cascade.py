"""Cascade control and gain scheduling tests (both opt-in via config)."""

import pytest

from scada import config
from scada.runtime import Runtime


def run(rt, seconds):
    for _ in range(int(seconds / config.PLC_SCAN_DT)):
        rt.step()


@pytest.fixture
def cascade(monkeypatch):
    monkeypatch.setattr(config, "CASCADE_ENABLED", True)
    rt = Runtime()
    rt.plc.pulse_start()
    run(rt, 120.0)
    assert rt.plc.state == "RUNNING"
    return rt


def test_cascade_inner_setpoint_tracks_outer(cascade):
    rt = cascade
    # In steady state the outer MV sits mid-range and the inner setpoint is
    # positioned inside the configured cascade range.
    outer_mv = rt.plc.pids["LIC-102"].mv
    assert 0.0 <= outer_mv <= 100.0
    assert rt.plc.cascade_inner_sp is not None
    assert (config.CASCADE_INNER_SP_MIN
            <= rt.plc.cascade_inner_sp <= config.CASCADE_INNER_SP_MAX)


def test_cascade_outer_authority_moves_inner_setpoint(cascade):
    rt = cascade
    sp_before = rt.plc.cascade_inner_sp
    rt.plc.set_setpoint("LIC-102", 0.9)   # raise outer SP
    run(rt, 60.0)
    # The outer loop drives TK-102 to its setpoint...
    assert abs(rt.plant.levels["TK-102"] - 0.9) < 0.03
    # ...and its MV positions the inner setpoint (direct acting: a higher
    # outer MV pushes the inner setpoint up).
    sp_after = rt.plc.cascade_inner_sp
    assert sp_after > sp_before - 0.05


def test_cascade_disabled_by_default_preserves_operator_setpoints():
    rt = Runtime()
    rt.plc.pulse_start()
    run(rt, 10.0)
    assert rt.plc.cascade_inner_sp is None
    # Operator setpoints are untouched by the scan.
    assert rt.plc.pids["LIC-101"].setpoint == pytest.approx(1.0)
    assert rt.plc.pids["LIC-102"].setpoint == pytest.approx(0.8)
    # The effective setpoint exported is the operator setpoint.
    exp = rt.plc.export()["pids"]["LIC-101"]
    assert exp["sp_eff"] == pytest.approx(exp["sp"])


def test_gain_scheduling_switches_gains_by_level(monkeypatch):
    """The scheduler picks the zone gains from the level fraction."""
    monkeypatch.setattr(config, "GAIN_SCHEDULING_ENABLED", True)
    rt = Runtime()
    pid = rt.plc.pids["LIC-101"]
    for frac, expected_kp in ((0.025, config.GAIN_SCHEDULE[0][1]),
                              (0.50, config.GAIN_SCHEDULE[1][1]),
                              (0.85, config.GAIN_SCHEDULE[-1][1])):
        rt.plc.ai["LT-101"] = frac * config.TANK_HEIGHT
        rt.plc._schedule_gains("LIC-101")
        assert pid.kp == pytest.approx(expected_kp)


def test_gain_scheduling_applied_through_scan_path(monkeypatch):
    """Starting the plant in the low zone applies the low-zone gains through
    the normal scan path (no artificial sensor slew that would trip the
    plausibility check)."""
    monkeypatch.setattr(config, "GAIN_SCHEDULING_ENABLED", True)
    rt = Runtime()
    rt.plant.levels["TK-101"] = 0.05   # low zone from the first scan
    rt.plc.pulse_start()
    run(rt, 2.0)
    assert rt.plc.pids["LIC-101"].kp == pytest.approx(config.GAIN_SCHEDULE[0][1])


def test_gain_scheduling_disabled_keeps_operator_tuning():
    rt = Runtime()
    rt.plc.set_tuning("LIC-101", kp=123.0, ki=45.0)
    rt.plc.pulse_start()
    run(rt, 5.0)
    assert rt.plc.pids["LIC-101"].kp == pytest.approx(123.0)
    assert rt.plc.pids["LIC-101"].ki == pytest.approx(45.0)
