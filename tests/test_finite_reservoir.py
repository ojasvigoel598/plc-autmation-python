"""Finite-reservoir mass balance and pipe/valve leak model tests."""

import pytest

from scada import config
from scada.runtime import Runtime


def run(rt, seconds):
    for _ in range(int(seconds / config.PLC_SCAN_DT)):
        rt.step()


# ---------------------------------------------------------------------------
# Finite reservoir
# ---------------------------------------------------------------------------
def test_reservoir_drains_while_pump_runs():
    rt = Runtime()
    rt.plc.pulse_start()
    level0 = rt.plant.reservoir_level
    run(rt, 20.0)
    assert rt.plant.reservoir_level < level0
    assert rt.snapshot()["reservoir_level"] == pytest.approx(
        rt.plant.reservoir_level)


def test_pump_starves_when_reservoir_empty():
    rt = Runtime()
    rt.plant.reservoir_level = 0.001   # effectively empty
    rt.plc.pulse_start()
    run(rt, 3.0)
    assert rt.plant.flows["P-101"] == 0.0   # no draw from an empty reservoir


def test_makeup_flow_refills_reservoir(monkeypatch):
    monkeypatch.setattr(config, "RESERVOIR_MAKEUP_FLOW", 0.02)  # > max draw
    rt = Runtime()
    rt.plc.pulse_start()
    run(rt, 20.0)
    # Replenished at least back to the starting level.
    assert rt.plant.reservoir_level >= config.INITIAL_RESERVOIR_LEVEL - 1e-9


def test_reservoir_level_clamped_to_tank_height():
    rt = Runtime()
    rt.plant.reservoir_level = config.RESERVOIR_HEIGHT + 10.0
    rt.step()
    assert rt.plant.reservoir_level <= config.RESERVOIR_HEIGHT


# ---------------------------------------------------------------------------
# Valve / pipe leaks
# ---------------------------------------------------------------------------
def test_valve_leak_adds_outflow_term_to_upstream():
    """A valve leak must remove exactly its flow from the upstream tank's
    rate of change, independent of any control action."""
    rt = Runtime()
    plant = rt.plant
    plant.set_actuators(pump_speed=0.5,
                        valve_open={"XV-101": 0.5, "XV-102": 0.5,
                                    "XV-103": 0.5})
    rt.step()
    levels = dict(plant.levels)
    before = plant._derivs(levels)["TK-101"]
    plant.valve_leaks["XV-101"] = 0.004
    after = plant._derivs(levels)["TK-101"]
    plant.valve_leaks["XV-101"] = 0.0
    assert after == pytest.approx(before - 0.004 / plant.tanks["TK-101"].area)


def test_valve_leak_detected_as_unexplained_loss():
    """A sustained valve leak surfaces through the mass-balance detector on
    the upstream tank (the leak is measured nowhere)."""
    rt = Runtime()
    rt.plc.pulse_start()
    run(rt, 30.0)
    rt.set_valve_leak("XV-101", 0.004)
    run(rt, config.LEAK_DETECT_WINDOW + 5.0)
    assert "TK-101" in rt._active_leaks
    assert any(e.tank == "TK-101" for e in rt.leak_store.recent(10))


def test_valve_leak_api_and_clear():
    from fastapi.testclient import TestClient
    from scada.server import create_app

    app = create_app(start_loop=False)
    with TestClient(app) as client:
        r = client.post("/api/faults/valve_leak",
                        json={"tag": "XV-102", "flow_m3s": 0.003})
        assert r.status_code == 200
        state = client.get("/api/state").json()
        assert state["faults"]["valve_leaks"]["XV-102"] == 0.003
        bad = client.post("/api/faults/valve_leak",
                          json={"tag": "XV-102", "flow_m3s": 9.0})
        assert bad.status_code == 422
        bad2 = client.post("/api/faults/valve_leak",
                           json={"tag": "XV-999", "flow_m3s": 0.001})
        assert bad2.status_code == 422
