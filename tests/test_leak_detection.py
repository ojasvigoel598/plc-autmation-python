"""Mass-balance leak detection: detector math and runtime integration."""

import pytest

from scada import config
from scada.leakdetect import MassBalanceLeakDetector
from scada.leaks import LeakStore
from scada.runtime import Runtime


def test_detector_recovers_known_leak_rate():
    det = MassBalanceLeakDetector()
    area = 0.6
    leak = 0.005            # m^3/s being "hidden" from the flow balance
    q_in, q_out = 0.012, 0.008
    dt = config.PLC_SCAN_DT
    level = 1.0
    t = 0.0
    rate = 0.0
    # True dynamics: dh/dt = (q_in - q_out - leak)/area
    while t < config.LEAK_DETECT_WINDOW * 2.5:
        level += (q_in - q_out - leak) / area * dt
        t += dt
        rate = det.update("TK-101", level=level, q_in=q_in, q_out=q_out,
                          overflow_volume=0.0, dt=dt, t=t, area=area)
    assert rate == pytest.approx(leak, abs=2e-4)


def test_detector_reports_zero_when_balance_closes():
    det = MassBalanceLeakDetector()
    area = 0.6
    q_in, q_out = 0.012, 0.010
    dt = config.PLC_SCAN_DT
    level = 1.0
    t = 0.0
    rate = 0.0
    while t < config.LEAK_DETECT_WINDOW * 2.5:
        level += (q_in - q_out) / area * dt
        t += dt
        rate = det.update("TK-101", level=level, q_in=q_in, q_out=q_out,
                          overflow_volume=0.0, dt=dt, t=t, area=area)
    assert rate < config.LEAK_DETECT_MIN_RATE


def _run(rt, seconds):
    n = int(seconds / config.PLC_SCAN_DT)
    for _ in range(n):
        rt.step()


def test_runtime_detects_and_resolves_injected_leak(tmp_path):
    rt = Runtime(leak_store=LeakStore(path=tmp_path / "leaks.json"))
    rt.plc.pulse_start()
    _run(rt, 120.0)
    assert rt.plc.state == "RUNNING"

    # Inject a sustained tank leak; the detector should catch it within a
    # couple of detection windows and record an event + raise an alarm.
    rt.set_leak("TK-102", 0.004)
    _run(rt, config.LEAK_DETECT_WINDOW * 3)
    assert "TK-102" in rt._active_leaks
    assert rt.leak_store.latest().tank == "TK-102"
    assert rt.leak_store.latest().status == "ACTIVE"
    assert any(a["tag"] == "LK-102" for a in rt.plc.export()["alarms"])
    snap = rt.snapshot()
    assert "TK-102" in snap["leaks"]["active"]
    assert snap["leaks"]["active"]["TK-102"] == pytest.approx(0.004, abs=2e-4)

    # Clear the leak; within a window the event resolves and the alarm clears.
    rt.set_leak("TK-102", 0.0)
    _run(rt, config.LEAK_DETECT_WINDOW * 3)
    assert "TK-102" not in rt._active_leaks
    assert rt.leak_store.latest().status == "RESOLVED"
    assert not any(a["tag"] == "LK-102" for a in rt.plc.export()["alarms"])


def test_no_false_positive_without_leak(tmp_path):
    rt = Runtime(leak_store=LeakStore(path=tmp_path / "leaks.json"))
    rt.plc.pulse_start()
    _run(rt, 150.0)
    assert rt._active_leaks == {}
    assert rt.leak_store.count() == 0
