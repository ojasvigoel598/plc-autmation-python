"""API token guard and operator audit log tests."""

import json

from fastapi.testclient import TestClient

from scada import config
from scada import server as server_mod


def test_control_endpoints_require_token_when_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "API_TOKEN", "sekret")
    monkeypatch.setenv("SCADA_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        # No token -> 401.
        assert client.post("/api/control/start").status_code == 401
        # Wrong token -> 401.
        r = client.post("/api/control/start",
                        headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401
        # Correct token -> 200.
        r = client.post("/api/control/start",
                        headers={"Authorization": "Bearer sekret"})
        assert r.status_code == 200
        # Fault injection is equally protected.
        assert client.post("/api/faults/pump/trip").status_code == 401
        # Read-only telemetry stays public.
        assert client.get("/api/state").status_code == 200
        assert client.get("/api/leaks").status_code == 200


def test_no_token_keeps_open_behaviour(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "API_TOKEN", "")
    monkeypatch.setenv("SCADA_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        assert client.post("/api/control/start").status_code == 200


def test_audit_log_records_control_actions(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "API_TOKEN", "")
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("SCADA_AUDIT_LOG", str(log))
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        client.post("/api/control/estop", json={"active": True})
        client.post("/api/faults/pump/trip")
        # Leave shared singleton state clean for other tests.
        client.post("/api/control/estop", json={"active": False})
        client.post("/api/faults/pump/reset")

    lines = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    paths = [l["path"] for l in lines]
    assert "/api/control/estop" in paths
    assert "/api/faults/pump/trip" in paths
    estop = next(l for l in lines if l["path"] == "/api/control/estop")
    assert json.loads(estop["body"]) == {"active": True}
    assert "client" in estop and "ts" in estop


def test_failed_auth_requests_are_audited(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "API_TOKEN", "sekret")
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("SCADA_AUDIT_LOG", str(log))
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        r = client.post("/api/control/estop", json={"active": True})
        assert r.status_code == 401
    lines = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    assert any(l["path"] == "/api/control/estop" for l in lines)
