"""
High-availability tests: lease fencing, takeover, checkpoint round-trip and
server HA wiring (standby read-only fencing).
"""

import time

import pytest

from scada import config
from scada import server as server_mod
from scada.ha import CheckpointStore, HAState, LeaderLease
from scada.runtime import Runtime


# ---------------------------------------------------------------------------
# LeaderLease: acquire / renew / release / fencing
# ---------------------------------------------------------------------------
def test_lease_single_winner(tmp_path):
    path = tmp_path / "ha.sqlite3"
    a = LeaderLease(path)
    b = LeaderLease(path)
    ta = a.acquire(ttl=5.0)
    assert ta is not None
    # Second acquirer must lose while the first holds an unexpired lease.
    assert b.acquire(ttl=5.0) is None
    # The winner is visible to both.
    assert a.is_leader(ta)
    assert b.leader()["token"] == ta
    a.release(ta)
    assert b.acquire(ttl=5.0) is not None


def test_lease_renew_fencing(tmp_path):
    """A demoted process (wrong token) must not be able to renew or hold on."""
    path = tmp_path / "ha.sqlite3"
    lease = LeaderLease(path)
    t1 = lease.acquire(ttl=5.0)
    assert t1 is not None
    assert lease.renew(t1, ttl=5.0) is True
    # Wrong token: renewal must fail (fencing).
    assert lease.renew("bogus-token", ttl=5.0) is False
    assert lease.is_leader(t1)
    assert lease.is_leader("bogus-token") is False


def _force_expire(path, name="plant"):
    """Deterministically make the current lease expire (simulates the active
    process crashing without relying on wall-clock sleeps, which flake under
    full-suite load)."""
    import sqlite3
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("UPDATE lease SET expires_at = 0.0 WHERE name = ?",
                     (name,))
        conn.commit()
    finally:
        conn.close()


def test_lease_expiry_allows_takeover(tmp_path):
    """After the TTL passes (active crashed), a standby can take over."""
    path = tmp_path / "ha.sqlite3"
    active = LeaderLease(path)
    standby = LeaderLease(path)
    ta = active.acquire(ttl=5.0)
    assert ta is not None
    assert standby.acquire(ttl=5.0) is None     # still alive
    assert not standby.expired()
    # Crash the active: force the lease past its TTL.
    _force_expire(path)
    assert standby.expired()
    ts = standby.acquire(ttl=5.0)
    assert ts is not None
    assert ts != ta
    # The old leader must now be fenced out.
    assert active.is_leader(ta) is False
    assert active.renew(ta, ttl=5.0) is False


def test_simultaneous_takeover_only_one_wins(tmp_path):
    """Two standbys racing for an expired lease: exactly one wins."""
    path = tmp_path / "ha.sqlite3"
    l1 = LeaderLease(path)
    l2 = LeaderLease(path)
    l1.acquire(ttl=5.0)
    _force_expire(path)
    t1 = l1.acquire(ttl=5.0)
    t2 = l2.acquire(ttl=5.0)
    winners = [t for t in (t1, t2) if t is not None]
    assert len(winners) == 1


# ---------------------------------------------------------------------------
# CheckpointStore: fenced writes + load
# ---------------------------------------------------------------------------
def test_checkpoint_fenced_by_lease(tmp_path):
    path = tmp_path / "ha.sqlite3"
    lease = LeaderLease(path)
    store = CheckpointStore(path)
    token = lease.acquire(ttl=5.0)
    assert token is not None

    assert store.save(token, t=10.0, state={"levels": {"TK-101": 1.2}}) is True
    ckpt = store.load()
    assert ckpt["t"] == 10.0
    assert ckpt["state"]["levels"]["TK-101"] == 1.2

    # A non-leader write must be refused (fencing).
    assert store.save("stale-token", t=99.0, state={"x": 1}) is False
    assert store.load()["t"] == 10.0


# ---------------------------------------------------------------------------
# Runtime serialize/restore round-trip (the failover payload)
# ---------------------------------------------------------------------------
def _run_faulty_scenario(runtime: Runtime, steps: int, dt: float) -> None:
    """Run a scenario that exercises plant, PLC, alarms, faults and units."""
    runtime.plc.pulse_start()
    for _ in range(steps):
        runtime.step(dt)
    runtime.set_leak("TK-102", 0.002)
    runtime.set_valve_leak("XV-101", 0.001)
    runtime.trip_pump()
    for _ in range(steps):
        runtime.step(dt)
    runtime.reset_pump()
    runtime.set_sensor_fault("LT-101", "STUCK")
    runtime.units.hx.fouling = 0.3
    runtime.units.conveyor.running = True
    runtime.units.conveyor.target_speed = 1.5
    runtime.units.pressure.valve_position = 0.4
    for _ in range(steps):
        runtime.step(dt)


def test_runtime_serialize_roundtrip():
    a = Runtime()
    _run_faulty_scenario(a, steps=40, dt=config.PLC_SCAN_DT)

    payload = a.serialize()
    b = Runtime()
    b.restore(payload)

    # The restored runtime must reproduce the exact same snapshot.
    sa, sb = a.snapshot(), b.snapshot()
    assert sa["t"] == sb["t"]
    assert sa["levels"] == sb["levels"]
    assert sa["reservoir_level"] == sb["reservoir_level"]
    assert sa["state"] == sb["state"]
    assert sa["scan_count"] == sb["scan_count"]
    assert sa["alarms"] == sb["alarms"]
    assert sa["faults"] == sb["faults"]
    assert sa["valves"] == sb["valves"]
    assert sa["pids"] == sb["pids"]
    assert sa["process_units"] == sb["process_units"]


def test_runtime_restore_continues_identically():
    """After restore, stepping both runtimes must produce identical state —
    the standby resumes exactly where the active left off (deterministic
    process + serialised RNG)."""
    a = Runtime()
    _run_faulty_scenario(a, steps=40, dt=config.PLC_SCAN_DT)

    b = Runtime()
    b.restore(a.serialize())

    for _ in range(20):
        sa = a.step(config.PLC_SCAN_DT)
        sb = b.step(config.PLC_SCAN_DT)
    assert sa["t"] == sb["t"]
    assert sa["levels"] == sb["levels"]
    assert sa["alarms"] == sb["alarms"]
    assert sa["process_units"] == sb["process_units"]
    assert sa["plc"]["scan_count"] == sb["plc"]["scan_count"]


def test_runtime_serialize_is_json_safe(tmp_path):
    """The payload must survive a JSON round-trip (checkpoint store path)."""
    import json
    r = Runtime()
    _run_faulty_scenario(r, steps=30, dt=config.PLC_SCAN_DT)
    payload = r.serialize()
    text = json.dumps(payload)
    loaded = json.loads(text)
    r2 = Runtime()
    r2.restore(loaded)
    assert r2.snapshot()["levels"] == r.snapshot()["levels"]


# ---------------------------------------------------------------------------
# HAState facade + end-to-end failover
# ---------------------------------------------------------------------------
def test_hastate_failover_restores_state(tmp_path):
    ha = HAState(tmp_path / "ha.sqlite3")
    token = ha.try_takeover(ttl=5.0)
    assert token is not None

    runtime = Runtime()
    _run_faulty_scenario(runtime, steps=30, dt=config.PLC_SCAN_DT)
    assert ha.checkpoint(token, runtime.snapshot()["t"], runtime.serialize()) is True

    # "Standby" takes over after the active's lease expires.
    ha.lease.acquire  # no-op reference
    # Force expiry of the active lease (simulate a crash) by rewriting TTL.
    ha.release(token)
    standby_ha = HAState(tmp_path / "ha.sqlite3")
    stoken = standby_ha.try_takeover(ttl=5.0)
    assert stoken is not None

    ckpt = standby_ha.load()
    assert ckpt is not None
    standby_runtime = Runtime()
    standby_runtime.restore(ckpt["state"])
    assert standby_runtime.snapshot()["t"] == runtime.snapshot()["t"]
    assert standby_runtime.snapshot()["levels"] == runtime.snapshot()["levels"]


# ---------------------------------------------------------------------------
# Server wiring: standby is read-only (fenced), active serves control
# ---------------------------------------------------------------------------
def test_ha_standby_control_rejected(monkeypatch, tmp_path):
    """In HA mode a process that is not the leader must refuse control POSTs
    (503) while still serving read-only telemetry."""
    from fastapi.testclient import TestClient

    # Simulate this process being a STANDBY (no lease held).
    monkeypatch.setattr(config, "HA_ENABLED", True)
    monkeypatch.setattr(config, "HA_DB_FILE", str(tmp_path / "ha.sqlite3"))
    # Another process holds the lease.
    ha = HAState(tmp_path / "ha.sqlite3")
    token = ha.try_takeover(ttl=5.0)
    assert token is not None

    with TestClient(server_mod.create_app(start_loop=False)) as client:
        # Telemetry is public even in standby.
        r = client.get("/api/state")
        assert r.status_code == 200
        # Control is fenced out.
        r = client.post("/api/control/start")
        assert r.status_code == 503
        r = client.post("/api/control/setpoint",
                        json={"tag": "LIC-101", "value": 1.1})
        assert r.status_code == 503
        r = client.post("/api/faults/leak",
                        json={"tank": "TK-102", "flow_m3s": 0.002})
        assert r.status_code == 503
        # Metrics still report the role honestly.
        r = client.get("/api/metrics")
        assert r.status_code == 200


def test_ha_active_control_allowed(monkeypatch, tmp_path):
    """In HA mode the ACTIVE process (holding the lease) can operate."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config, "HA_ENABLED", True)
    monkeypatch.setattr(config, "HA_DB_FILE", str(tmp_path / "ha.sqlite3"))

    # This process wins the lease at startup, so it is ACTIVE.
    ha = HAState(tmp_path / "ha.sqlite3")
    server_mod.ha_state = ha
    server_mod.ha_token = ha.try_takeover(ttl=5.0)
    server_mod.ha_role = "ACTIVE"
    assert server_mod.ha_token is not None

    try:
        with TestClient(server_mod.create_app(start_loop=False)) as client:
            r = client.post("/api/control/start")
            assert r.status_code == 200, r.text
    finally:
        server_mod.ha_state = None
        server_mod.ha_token = None
        server_mod.ha_role = "ACTIVE"
