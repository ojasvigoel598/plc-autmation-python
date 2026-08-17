"""Leak REST API tests: listing, latest, by-id, injection, validation."""

from fastapi.testclient import TestClient

from scada import server
from scada.leaks import LeakStore
from scada.server import create_app


def make_client(tmp_path):
    # Point the singleton's store at a temp file so tests never write the
    # repo's data/ directory.
    server.runtime.leak_store = LeakStore(path=tmp_path / "leaks.json")
    app = create_app(start_loop=False)
    return TestClient(app)


def test_leaks_empty(tmp_path):
    with make_client(tmp_path) as c:
        r = c.get("/api/leaks")
        assert r.status_code == 200
        assert r.json() == {"count": 0, "events": []}
        assert c.get("/api/leaks/latest").json()["event"] is None


def test_leak_list_latest_and_by_id(tmp_path):
    with make_client(tmp_path) as c:
        s = server.runtime.leak_store
        s.record(tank="TK-102", rate_m3s=0.004, level_before=0.8,
                 source="mass-balance", alarm_tag="LK-102", t_start=10.0)
        e2 = s.record(tank="TK-101", rate_m3s=0.001, level_before=1.0,
                      source="mass-balance", alarm_tag="LK-101", t_start=20.0)

        latest = c.get("/api/leaks/latest").json()
        assert latest["event"]["tank"] == "TK-101"

        listed = c.get("/api/leaks").json()
        assert listed["count"] == 2
        assert [e["tank"] for e in listed["events"]] == ["TK-101", "TK-102"]

        one = c.get(f"/api/leaks/{e2.id}")
        assert one.status_code == 200
        assert one.json()["tank"] == "TK-101"
        assert one.json()["location"] == "TK-101 (tank)"

        assert c.get("/api/leaks/does-not-exist").status_code == 404


def test_leak_injection_endpoint(tmp_path):
    with make_client(tmp_path) as c:
        ok = c.post("/api/faults/leak",
                    json={"tank": "TK-102", "flow_m3s": 0.004})
        assert ok.status_code == 200
        assert server.runtime.leaks["TK-102"] == 0.004

        # validation rejects out-of-range rate and unknown tag
        assert c.post("/api/faults/leak",
                      json={"tank": "TK-102", "flow_m3s": 9.9}).status_code == 422
        assert c.post("/api/faults/leak",
                      json={"tank": "NOPE", "flow_m3s": 0.001}).status_code == 422
