"""Tests for observability endpoints and the event-driven architecture."""

from dataclasses import asdict

from fastapi.testclient import TestClient

from scada import server as server_mod
from scada.events import EventStore, EventType, Event, CommandEnvelope, CommandStatus
from pathlib import Path


def test_healthz():
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"


def test_readyz_when_not_running():
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        r = client.get("/readyz")
        # Without the sim loop, readiness should be false (503).
        assert r.status_code == 503


def test_metrics_endpoint():
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        r = client.get("/api/metrics")
        assert r.status_code == 200
        data = r.json()
        assert "uptime_s" in data
        assert "scan_count" in data
        assert "ws_clients" in data
        assert "process_units" in data
        assert "alarms_active" in data


def test_events_endpoint():
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        r = client.get("/api/events")
        assert r.status_code == 200
        data = r.json()
        assert "events" in data
        assert "summary" in data
        assert "total_events" in data["summary"]


# ---------------------------------------------------------------------------
# EventStore unit tests
# ---------------------------------------------------------------------------
def test_event_store_emit_and_recent(tmp_path):
    store = EventStore(path=tmp_path / "events.jsonl")
    store.emit(Event(event_type=EventType.STATE_TRANSITION,
                     source="PLC", tag="LIC-101", message="IDLE -> STARTING"))
    store.emit(Event(event_type=EventType.ALARM_RAISE,
                     source="PLC", tag="TSH-101", message="High temperature",
                     severity="HIGH"))
    events = store.recent(10)
    assert len(events) == 2
    assert events[-1].tag == "TSH-101"


def test_event_store_persistence(tmp_path):
    path = tmp_path / "events.jsonl"
    store1 = EventStore(path=path)
    store1.emit(Event(event_type=EventType.ALARM_RAISE, message="test"))
    store1 = None  # drop reference

    store2 = EventStore(path=path)
    assert len(store2.recent(10)) == 1


def test_command_idempotency():
    store = EventStore()
    cmd = CommandEnvelope(command_id="test-123",
                          command_type="START",
                          source_ip="127.0.0.1")
    assert store.check_duplicate("test-123") is False
    store.record_command(cmd)
    assert store.check_duplicate("test-123") is True
    # Second record is idempotent.
    store.record_command(cmd)


def test_event_store_summary():
    store = EventStore()
    store.emit(Event(event_type=EventType.ALARM_RAISE, message="a"))
    store.emit(Event(event_type=EventType.ALARM_ACK, message="b"))
    store.emit(Event(event_type=EventType.STATE_TRANSITION, message="c"))
    summary = store.summary()
    assert summary["total_events"] == 3
    assert summary["by_type"]["ALARM_RAISE"] == 1
    assert summary["by_type"]["ALARM_ACK"] == 1
