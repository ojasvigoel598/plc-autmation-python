"""Modbus TCP tests: register map, write routing, and wire round-trips."""

import math
import socket
import struct

import pytest

from scada import modbus_map as mm
from scada.modbus_server import ModbusServer
from scada.plc import PLC


# ---------------------------------------------------------------------------
# Register map: scaling and sentinels
# ---------------------------------------------------------------------------
def test_level_scaling_roundtrip():
    assert mm.encode(1.0, mm.LEVEL_SCALE) == 1000
    assert mm.decode(1000, mm.LEVEL_SCALE) == 1.0
    assert mm.encode(0.5, mm.LEVEL_SCALE) == 500


def test_percent_scaling_roundtrip():
    assert mm.encode(57.0, mm.PCT_SCALE) == 570
    assert mm.decode(570, mm.PCT_SCALE) == 57.0


def test_nan_maps_to_invalid_sentinel():
    assert mm.encode(float("nan"), mm.LEVEL_SCALE) == mm.INVALID_COUNT
    assert math.isnan(mm.decode(mm.INVALID_COUNT, mm.LEVEL_SCALE))


def test_encode_clamps_to_int16_range():
    assert mm.encode(1e6, mm.LEVEL_SCALE) == 32767
    assert mm.encode(-1e6, mm.LEVEL_SCALE) == -32768


def test_tables_align_with_plc_image():
    plc = PLC()
    assert mm.read_coils(plc) == [False, False, False, True]
    assert mm.read_discrete_inputs(plc) == [False] * 5
    assert len(mm.read_input_registers(plc)) == 6
    assert len(mm.read_holding_registers(plc)) == 13


# ---------------------------------------------------------------------------
# Write routing through the PLC command interface
# ---------------------------------------------------------------------------
def test_write_setpoint_clamps_to_tank_height():
    plc = PLC()
    accepted = mm.write_holding_register(plc, 4, 9999)  # LIC-101 setpoint
    assert accepted == mm.encode(2.0, mm.LEVEL_SCALE)   # clamped to 2.0 m
    assert plc.pids["LIC-101"].setpoint == 2.0


def test_write_mode_enum():
    plc = PLC()
    assert mm.write_holding_register(plc, 6, 1) == 1     # LIC-101 -> MANUAL
    assert plc.loop_mode["LIC-101"] == "MANUAL"
    assert mm.write_holding_register(plc, 6, 0) == 0     # back to AUTO
    assert plc.loop_mode["LIC-101"] == "AUTO"


def test_write_invalid_mode_rejected():
    plc = PLC()
    with pytest.raises(ValueError):
        mm.write_holding_register(plc, 6, 7)


def test_write_read_only_output_rejected():
    plc = PLC()
    with pytest.raises(LookupError):
        mm.write_holding_register(plc, 0, 500)  # AQ P-101 is read-only


def test_write_out_of_range_rejected():
    plc = PLC()
    with pytest.raises(LookupError):
        mm.write_holding_register(plc, 999, 1)


# ---------------------------------------------------------------------------
# Wire round-trip over a real TCP socket
# ---------------------------------------------------------------------------
@pytest.fixture
def server():
    plc = PLC()
    srv = ModbusServer(lambda: plc, host="127.0.0.1", port=0)
    srv.start()
    yield srv, plc
    srv.stop()


def _xact(port: int, txid: int, pdu: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
        frame = struct.pack(">HHHB", txid, 0, 1 + len(pdu), 1) + pdu
        s.sendall(frame)
        header = s.recv(7)
        _t, _p, length, _u = struct.unpack(">HHHB", header)
        return s.recv(length - 1)


def test_read_holding_registers_over_wire(server):
    srv, _plc = server
    resp = _xact(srv.bound_port, 1, struct.pack(">BHH", 0x03, 4, 2))
    assert resp[0] == 0x03
    assert resp[1] == 4  # byte count
    assert struct.unpack(">hh", resp[2:]) == (1000, 800)


def test_read_coils_and_discrete_over_wire(server):
    srv, plc = server
    plc.q["Q0.0_PUMP"] = True
    resp = _xact(srv.bound_port, 2, struct.pack(">BHH", 0x01, 0, 4))
    assert resp[0] == 0x01
    assert resp[1] == 1
    assert resp[2] & 0x01 == 1  # Q0.0_PUMP is bit 0
    resp = _xact(srv.bound_port, 3, struct.pack(">BHH", 0x02, 0, 5))
    assert resp[0] == 0x02


def test_write_single_register_over_wire(server):
    srv, plc = server
    resp = _xact(srv.bound_port, 4, struct.pack(">BHH", 0x06, 4, 1500))
    assert struct.unpack(">Hh", resp[1:5]) == (4, 1500)
    assert plc.pids["LIC-101"].setpoint == 1.5


def test_write_read_only_register_returns_exception(server):
    srv, _plc = server
    resp = _xact(srv.bound_port, 5, struct.pack(">BHH", 0x06, 0, 500))
    assert resp[0] == 0x06 | 0x80
    assert resp[1] == 0x02  # ILLEGAL DATA ADDRESS


def test_write_coil_returns_exception(server):
    srv, _plc = server
    resp = _xact(srv.bound_port, 6, struct.pack(">BHH", 0x05, 0, 0xFF00))
    assert resp[0] == 0x05 | 0x80
    assert resp[1] == 0x02


def test_write_multiple_registers_over_wire(server):
    srv, plc = server
    pdu = struct.pack(">BHHB", 0x10, 4, 2, 4) + struct.pack(">hh", 1400, 900)
    resp = _xact(srv.bound_port, 7, pdu)
    assert struct.unpack(">HH", resp[1:5]) == (4, 2)
    assert plc.pids["LIC-101"].setpoint == 1.4
    assert plc.pids["LIC-102"].setpoint == 0.9


def test_unsupported_function_returns_exception(server):
    srv, _plc = server
    resp = _xact(srv.bound_port, 8, bytes([0x2B, 0x0E, 0x01, 0x00]))
    assert resp[0] == 0x2B | 0x80
    assert resp[1] == 0x01  # ILLEGAL FUNCTION
