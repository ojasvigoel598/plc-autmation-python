"""SCADA REST API tests (validation and control routing)."""

from fastapi.testclient import TestClient

from scada.server import create_app


def make_client():
    app = create_app(start_loop=False)
    return TestClient(app)


def test_index_serves_ui():
    with make_client() as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]


def test_state_endpoint_shape():
    with make_client() as client:
        r = client.get("/api/state")
        assert r.status_code == 200
        data = r.json()
        for key in ("state", "levels", "flows", "pids", "valves", "pump",
                    "alarms", "faults", "t"):
            assert key in data


def test_start_stop_reset():
    with make_client() as client:
        assert client.post("/api/control/start").json()["ok"] is True
        assert client.post("/api/control/stop").json()["ok"] is True
        assert client.post("/api/control/reset").json()["ok"] is True


def test_setpoint_validation():
    with make_client() as client:
        ok = client.post("/api/control/setpoint",
                         json={"tag": "LIC-102", "value": 1.2})
        assert ok.status_code == 200
        bad = client.post("/api/control/setpoint",
                          json={"tag": "LIC-102", "value": 99.0})
        assert bad.status_code == 422


def test_mode_validation():
    with make_client() as client:
        ok = client.post("/api/control/mode",
                         json={"tag": "LIC-102", "mode": "MANUAL", "manual_mv": 42})
        assert ok.status_code == 200
        bad = client.post("/api/control/mode",
                          json={"tag": "LIC-102", "mode": "BOGUS"})
        assert bad.status_code == 422


def test_manual_valve_range_validation():
    with make_client() as client:
        bad = client.post("/api/control/manual_valve",
                          json={"tag": "XV-102", "position": 150.0})
        assert bad.status_code == 422
        ok = client.post("/api/control/manual_valve",
                         json={"tag": "XV-102", "position": 30.0})
        assert ok.status_code == 200


def test_estop_roundtrip():
    with make_client() as client:
        assert client.post("/api/control/estop",
                           json={"active": True}).json()["estop"] is True
        state = client.get("/api/state").json()
        assert state["estop"] is True
        client.post("/api/control/estop", json={"active": False})


def test_sensor_fault_inject_and_clear():
    with make_client() as client:
        r = client.post("/api/faults/sensor",
                        json={"tag": "LT-102", "fault": "FAIL_HIGH"})
        assert r.status_code == 200
        state = client.get("/api/state").json()
        assert state["sensors"]["LT-102"] == "FAIL_HIGH"
        r2 = client.post("/api/faults/sensor/clear", json={"tag": "LT-102"})
        assert r2.status_code == 200
        assert client.get("/api/state").json()["sensors"]["LT-102"] == "OK"


def test_pump_and_valve_fault_endpoints():
    with make_client() as client:
        assert client.post("/api/faults/pump/trip").json()["ok"] is True
        assert client.post("/api/faults/pump/reset").json()["ok"] is True
        assert client.post("/api/faults/valve/stick",
                           json={"tag": "XV-102"}).json()["ok"] is True
        assert client.post("/api/faults/valve/unstick",
                           json={"tag": "XV-102"}).json()["ok"] is True
        assert client.post("/api/faults/disturbance",
                           json={"tank": "TK-102", "flow_m3s": 0.005,
                                 "duration": 30}).json()["ok"] is True


def test_invalid_tag_rejected():
    with make_client() as client:
        r = client.post("/api/control/setpoint",
                        json={"tag": "NOT-A-LOOP", "value": 1.0})
        assert r.status_code == 422
