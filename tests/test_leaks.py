"""Leak event store tests: lifecycle, ordering, persistence, resilience."""

import json

from scada.leaks import LeakStore, severity_for


def make_store(tmp_path):
    return LeakStore(path=tmp_path / "leaks.json")


def test_record_creates_active_event(tmp_path):
    store = make_store(tmp_path)
    ev = store.record(tank="TK-102", rate_m3s=0.004, level_before=0.8,
                      source="mass-balance", alarm_tag="LK-102", t_start=42.0)
    assert ev.status == "ACTIVE"
    assert ev.location == "TK-102 (tank)"
    assert ev.t_end is None
    assert store.latest().id == ev.id


def test_resolve_transitions_to_resolved(tmp_path):
    store = make_store(tmp_path)
    ev = store.record(tank="TK-101", rate_m3s=0.001, level_before=1.0,
                      source="mass-balance")
    assert store.resolve(ev.id, t_end=60.0) is True
    got = store.get(ev.id)
    assert got.status == "RESOLVED"
    assert got.t_end == 60.0
    # resolving twice is a no-op
    assert store.resolve(ev.id, t_end=61.0) is False


def test_recent_returns_latest_first_and_caps(tmp_path):
    store = make_store(tmp_path)
    for i in range(35):
        store.record(tank="TK-103", rate_m3s=0.0004, level_before=0.5,
                     source="mass-balance", t_start=float(i))
    rec = store.recent(30)
    assert len(rec) == 30
    # newest first (highest t_start at index 0)
    assert rec[0].t_start == 34.0
    assert rec[-1].t_start == 5.0


def test_persistence_roundtrip(tmp_path):
    p = tmp_path / "leaks.json"
    store = LeakStore(path=p)
    store.record(tank="TK-102", rate_m3s=0.005, level_before=0.7,
                 source="mass-balance", alarm_tag="LK-102")
    # A fresh store reading the same file must see the event.
    store2 = LeakStore(path=p)
    assert store2.count() == 1
    assert store2.latest().tank == "TK-102"


def test_corrupt_file_degrades_to_empty(tmp_path):
    p = tmp_path / "leaks.json"
    p.write_text("{ not valid json", encoding="utf-8")
    store = LeakStore(path=p)
    assert store.count() == 0


def test_severity_thresholds():
    assert severity_for(0.0004) == "LOW"
    assert severity_for(0.002) == "MODERATE"
    assert severity_for(0.012) == "HIGH"


def test_get_unknown_id_returns_none(tmp_path):
    store = make_store(tmp_path)
    assert store.get("does-not-exist") is None
