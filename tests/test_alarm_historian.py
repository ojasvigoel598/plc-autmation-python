"""Alarm journal persistence, historian retention and corrupt-store tests."""

import pytest

from scada.historian import TrendStore


def test_alarm_events_persist_and_survive_restart(tmp_path):
    path = tmp_path / "trends.sqlite3"
    store = TrendStore(path)
    store.record_alarm_event("ESTOP", "Emergency stop active", "CRITICAL",
                             "RAISE", 12.5)
    store.record_alarm_event("ESTOP", "Emergency stop active", "CRITICAL",
                             "ACK", 13.0)
    store.close()

    reopened = TrendStore(path)
    events = reopened.alarm_history(10)
    assert [e["action"] for e in events] == ["ACK", "RAISE"]
    assert events[0]["tag"] == "ESTOP"
    assert events[0]["priority"] == "CRITICAL"
    assert events[0]["t"] == pytest.approx(13.0)
    reopened.close()


def test_alarm_manager_emits_transitions():
    from scada.plc import PLC

    plc = PLC()
    events = []
    plc.alarms.set_event_callback(lambda action, alarm, t:
                                  events.append((action, alarm.tag)))
    plc.alarms.raise_alarm("LSH-101", "TK-101 HIGH level", "HIGH", 1.0)
    plc.alarms.acknowledge("LSH-101", 2.0)
    plc.alarms.clear_alarm("LSH-101", 3.0)
    assert events == [("RAISE", "LSH-101"), ("ACK", "LSH-101"),
                      ("CLEAR", "LSH-101")]


def test_historian_prunes_oldest_rows(tmp_path, monkeypatch):
    import scada.historian as hmod

    monkeypatch.setattr(hmod.config, "HISTORIAN_MAX_ROWS", 50)
    path = tmp_path / "trends.sqlite3"
    store = TrendStore(path)
    recs = [{"t": float(i), "h1": float(i)} for i in range(100)]
    store._inserts = 9900          # next insert crosses the prune threshold
    store.insert(recs)
    assert store.count() <= 50
    # The newest rows survive; the oldest are pruned.
    points = store.range("h1", 0.0, 1e9, 10000)
    assert points[0][1] == pytest.approx(50.0)
    store.close()


def test_corrupt_store_quarantined_and_recreated(tmp_path):
    path = tmp_path / "trends.sqlite3"
    path.write_bytes(b"this is not a sqlite database at all..........")
    store = TrendStore(path)
    assert store.count() == 0
    store.close()
    # The corrupt file was quarantined (kept as evidence), not deleted.
    quarantined = [p for p in tmp_path.iterdir() if ".corrupt-" in p.name]
    assert len(quarantined) == 1


def test_alarm_history_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from scada import server as server_mod

    store = TrendStore(path=tmp_path / "trends.sqlite3")
    monkeypatch.setattr(server_mod.runtime, "historian", store)
    # Wire the same durable-journal callback the lifespan installs.
    server_mod.runtime.plc.alarms.set_event_callback(
        lambda action, alarm, t: store.record_alarm_event(
            alarm.tag, alarm.message, alarm.priority, action, t))
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        # Raise an alarm through the PLC so the journal gets an event
        # (the loop is off, so drive a scan by hand).
        client.post("/api/control/estop", json={"active": True})
        server_mod.runtime.step()
        client.post("/api/control/estop", json={"active": False})
        r = client.get("/api/alarms/history")
        assert r.status_code == 200
        tags = {e["tag"] for e in r.json()["events"]}
        assert "ESTOP" in tags
