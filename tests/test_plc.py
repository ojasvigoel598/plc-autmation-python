"""PLC engine and IEC 61131-3 function block tests."""

import math

import pytest

from scada import config
from scada.plc import (TON, TOF, TP, CTU, CTD, SR, RS, RTrig, FTrig, PLC,
                       AlarmManager)


# ---------------------------------------------------------------------------
# Function blocks
# ---------------------------------------------------------------------------
def test_ton_on_delay():
    t = TON(1.0)
    assert t.update(False, 0.1) is False
    for _ in range(9):
        t.update(True, 0.1)
    assert t.q is False
    assert t.update(True, 0.1) is True


def test_tof_off_delay():
    t = TOF(1.0)
    assert t.update(True, 0.1) is True
    for _ in range(9):
        t.update(False, 0.1)
    assert t.q is True
    assert t.update(False, 0.1) is False


def test_tp_pulse():
    t = TP(0.5)
    assert t.update(False, 0.1) is False
    assert t.update(True, 0.1) is True
    for _ in range(4):
        t.update(True, 0.1)
    assert t.update(True, 0.1) is False


def test_ctu_rising_edge():
    c = CTU(3)
    c.update(True); c.update(False)   # one rising edge
    c.update(True); c.update(False)   # second rising edge
    assert c.cv == 2
    assert c.q is False
    c.update(True)                    # third rising edge -> done
    assert c.q is True
    c.update(True, reset=True)
    assert c.cv == 0 and c.q is False


def test_ctd_rising_edge():
    c = CTD(2)
    c.update(True); c.update(False)
    assert c.cv == 1
    c.update(True)
    assert c.q is True


def test_sr_vs_rs_dominance():
    sr = SR()
    rs = RS()
    # both inputs asserted: SR sets, RS resets.
    assert sr.update(True, True) is True
    assert rs.update(True, True) is False


def test_edge_detectors():
    r = RTrig()
    f = FTrig()
    assert r.update(True) is True
    assert r.update(True) is False
    assert f.update(True) is False
    assert f.update(False) is True


# ---------------------------------------------------------------------------
# Alarm manager
# ---------------------------------------------------------------------------
def test_alarm_raise_ack_clear():
    am = AlarmManager()
    am.raise_alarm("X", "boom", "HIGH", t=1.0)
    assert am.active["X"].state == "ACTIVE"
    am.acknowledge("X", t=2.0)
    assert am.active["X"].state == "ACKED"
    am.clear_alarm("X", t=3.0)
    assert "X" not in am.active
    assert am.history[0].state == "CLEARED"


def test_alarm_raise_is_deduplicated():
    am = AlarmManager()
    am.raise_alarm("X", "a", "HIGH", 0.0)
    am.raise_alarm("X", "b", "CRITICAL", 0.0)
    assert len(am.active) == 1


# ---------------------------------------------------------------------------
# PLC engine
# ---------------------------------------------------------------------------
def _plc_running(plc, seconds=5.0):
    plc.pulse_start()
    n = int(seconds / config.PLC_SCAN_DT)
    # keep sensors at mid-range so no level alarm interferes
    for _ in range(n):
        plc.ai["LT-101"] = 1.0
        plc.ai["LT-102"] = 0.8
        plc.ai["LT-103"] = 0.4
        # simulate the motor auxiliary contact following the run coil
        plc.set_pump_feedback(plc.q["Q0.0_PUMP"])
        plc.set_valve_feedback("XV-101", plc.aq["XV-101"])
        plc.set_valve_feedback("XV-102", plc.aq["XV-102"])
        plc.set_valve_feedback("XV-103", plc.aq["XV-103"])
        plc.scan(config.PLC_SCAN_DT)
    return plc


def test_scan_cycle_reaches_running():
    plc = PLC()
    _plc_running(plc)
    assert plc.state == PLC.STATE_RUNNING
    assert plc.scan_count > 0


def test_estop_has_highest_authority():
    plc = PLC()
    _plc_running(plc)
    plc.set_estop(True)
    plc.scan(config.PLC_SCAN_DT)
    assert plc.state == PLC.STATE_E_STOPPED
    assert plc.aq["P-101"] == 0.0
    assert plc.q["Q0.0_PUMP"] is False


def test_sensor_nan_latches_fault():
    plc = PLC()
    _plc_running(plc)
    plc.ai["LT-102"] = float("nan")
    plc.scan(config.PLC_SCAN_DT)
    assert plc._sensor_fault_latch["LT-102"].q is True
    assert "LT-102_FAULT" in plc.alarms.active


def test_sensor_fault_forces_loop_to_manual_safe():
    plc = PLC()
    _plc_running(plc)
    plc.ai["LT-102"] = float("nan")
    plc.scan(config.PLC_SCAN_DT)
    assert plc.loop_mode["LIC-102"] == "MANUAL"
    assert plc.pids["LIC-102"].mv == 0.0


def test_high_high_level_interlock_trips_feed():
    plc = PLC()
    _plc_running(plc)
    plc.ai["LT-101"] = config.TANK_HEIGHT * (config.LEVEL_HIHI + 0.02)
    plc.scan(config.PLC_SCAN_DT)
    assert plc.aq["P-101"] == 0.0
    assert "LSHH-101" in plc.alarms.active


def test_reset_clears_tripped_state():
    plc = PLC()
    _plc_running(plc)
    plc.set_estop(True)
    plc.scan(config.PLC_SCAN_DT)
    assert plc.state == PLC.STATE_E_STOPPED
    plc.set_estop(False)
    plc.pulse_reset()
    plc.scan(config.PLC_SCAN_DT)
    assert plc.state == PLC.STATE_IDLE
