"""Rate limiting, WebSocket caps and Modbus allowlist tests."""

import socket
import struct

import pytest
from fastapi.testclient import TestClient

from scada import config
from scada import server as server_mod
from scada.modbus_server import ModbusServer
from scada.plc import PLC


def test_control_rate_limit_enforced(monkeypatch):
    monkeypatch.setattr(config, "CONTROL_RATE_LIMIT", 3)
    server_mod._rate_buckets.clear()
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        for _ in range(3):
            assert client.post("/api/control/start").status_code == 200
        assert client.post("/api/control/start").status_code == 429


def test_control_rate_limit_not_triggered_under_budget(monkeypatch):
    monkeypatch.setattr(config, "CONTROL_RATE_LIMIT", 1000)
    server_mod._rate_buckets.clear()
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        for _ in range(5):
            assert client.post("/api/control/start").status_code == 200


def test_ws_client_cap_enforced(monkeypatch):
    monkeypatch.setattr(config, "MAX_WS_CLIENTS", 1)
    with TestClient(server_mod.create_app(start_loop=False)) as client:
        with client.websocket_connect("/ws") as ws1:
            assert ws1.receive_json()["type"] == "config"
            # A second client must be refused while the first is connected.
            with pytest.raises(Exception):
                with client.websocket_connect("/ws") as ws2:
                    ws2.receive_json()


def test_modbus_allowlist_rejects_unknown_client(monkeypatch):
    plc = PLC()
    monkeypatch.setattr(config, "MODBUS_ALLOWED_CLIENTS", {"10.99.99.99"})
    srv = ModbusServer(lambda: plc, host="127.0.0.1", port=0)
    srv.start()
    try:
        # 127.0.0.1 is not in the allowlist -> the connection is dropped.
        with socket.create_connection(("127.0.0.1", srv.bound_port),
                                      timeout=3) as s:
            pdu = struct.pack(">BHH", 0x03, 0, 2)
            s.sendall(struct.pack(">HHHB", 1, 0, 1 + len(pdu), 1) + pdu)
            # Server closes without a response; recv returns b"" (EOF).
            assert s.recv(16) == b""
    finally:
        srv.stop()


def test_modbus_allowlist_allows_configured_client(monkeypatch):
    plc = PLC()
    monkeypatch.setattr(config, "MODBUS_ALLOWED_CLIENTS", {"127.0.0.1"})
    srv = ModbusServer(lambda: plc, host="127.0.0.1", port=0)
    srv.start()
    try:
        with socket.create_connection(("127.0.0.1", srv.bound_port),
                                      timeout=3) as s:
            pdu = struct.pack(">BHH", 0x03, 0, 2)
            s.sendall(struct.pack(">HHHB", 1, 0, 1 + len(pdu), 1) + pdu)
            header = s.recv(7)
            _t, _p, length, _u = struct.unpack(">HHHB", header)
            resp = s.recv(length - 1)
            assert resp[0] == 0x03
    finally:
        srv.stop()
