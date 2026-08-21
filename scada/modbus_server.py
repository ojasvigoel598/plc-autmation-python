"""Modbus TCP server exposing the PLC I/O image to external clients.

Runs in its own thread alongside the FastAPI sim loop.  A real SCADA,
historian, or PLC master can poll the plant over standard Modbus TCP
(port 502) using the register map in ``modbus_map``.

Implemented with the Python standard library only (``socket`` + ``struct`` +
``socketserver``) so the server adds no runtime dependency — consistent with
the rest of the project's stdlib-only persistence tooling.

Wire semantics
--------------
Modbus TCP wraps the PDU in a 7-byte MBAP header:

    TransactionId(2) ProtocolId(2) Length(2) UnitId(1) | PDU...

The PDU addresses are zero-based within each data model, matching the
offsets in ``modbus_map`` directly.

Authority: read function codes (0x01..0x04) return the live I/O image; write
function codes are handled in a separate pass and routed through the PLC's
validated command setters.
"""

from __future__ import annotations

import logging
import socket
import socketserver
import struct
import threading
from typing import Callable

from . import config
from . import modbus_map as mm

logger = logging.getLogger("scada.modbus")

# MBAP header = TransactionId(2) ProtocolId(2) Length(2) UnitId(1)
MBAP = struct.Struct(">HHHB")

# Function codes
FC_READ_COILS = 0x01
FC_READ_DISCRETE = 0x02
FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04
FC_WRITE_COIL = 0x05
FC_WRITE_REGISTER = 0x06
FC_WRITE_COILS = 0x0F
FC_WRITE_REGISTERS = 0x10

# Exception codes
EX_ILLEGAL_FUNCTION = 0x01
EX_ILLEGAL_ADDRESS = 0x02
EX_ILLEGAL_VALUE = 0x03

_MAX_BITS = 2000       # Modbus spec limit for bit reads
_MAX_REGISTERS = 125   # Modbus spec limit for register reads


class ModbusError(Exception):
    """A request that must be answered with a Modbus exception response."""

    def __init__(self, code: int) -> None:
        self.code = code


def _pack_bits(bits: list[bool]) -> bytes:
    """Pack booleans into Modbus coil bytes (LSB-first within each byte)."""
    out = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for j, b in enumerate(bits[i:i + 8]):
            if b:
                byte |= 1 << j
        out.append(byte)
    return bytes(out)


class ModbusServer:
    """Threaded Modbus TCP server bound to the live PLC."""

    def __init__(self, plc_getter: Callable, host: str = "0.0.0.0",
                 port: int = 502, lock: threading.Lock | None = None) -> None:
        self._plc_getter = plc_getter
        self._lock = lock
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None
        self.host = host
        self.port = port
        # Client IP allowlist; empty set = allow all (backwards-compatible).
        self.allowed_clients: set[str] = set(config.MODBUS_ALLOWED_CLIENTS)

    def _client_allowed(self, client_ip: str) -> bool:
        return not self.allowed_clients or client_ip in self.allowed_clients

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._server is not None:
            return
        handler = _make_handler(self)
        self._server = socketserver.ThreadingTCPServer(
            (self.host, self.port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="modbus-tcp", daemon=True)
        self._thread.start()
        logger.info("Modbus TCP server listening on %s:%s", self.host, self.port)

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def bound_port(self) -> int | None:
        """The actual TCP port (useful when started on an ephemeral port 0)."""
        if self._server is None:
            return None
        return self._server.server_address[1]

    # -- PLC access (serialised against the sim loop) ---------------------
    def _plc(self):
        plc = self._plc_getter()
        if self._lock is not None:
            self._lock.acquire()
        return plc

    def _release(self) -> None:
        if self._lock is not None:
            self._lock.release()

    # -- PDU dispatch -----------------------------------------------------
    def handle_request(self, txid: int, unit: int, pdu: bytes) -> bytes:
        """Dispatch one PDU and return the response PDU (FC already first)."""
        if not pdu:
            raise ModbusError(EX_ILLEGAL_FUNCTION)
        fc = pdu[0]
        if fc == FC_READ_COILS:
            return self._read_coils(pdu)
        if fc == FC_READ_DISCRETE:
            return self._read_discrete(pdu)
        if fc == FC_READ_HOLDING:
            return self._read_holding(pdu)
        if fc == FC_READ_INPUT:
            return self._read_input(pdu)
        if fc == FC_WRITE_COIL:
            return self._write_coil(pdu)
        if fc == FC_WRITE_REGISTER:
            return self._write_register(pdu)
        if fc == FC_WRITE_COILS:
            return self._write_coils(pdu)
        if fc == FC_WRITE_REGISTERS:
            return self._write_registers(pdu)
        raise ModbusError(EX_ILLEGAL_FUNCTION)

    # -- read handlers ----------------------------------------------------
    def _read_coils(self, pdu: bytes) -> bytes:
        plc = self._plc()
        try:
            start, count = self._read_bits_request(pdu)
            table = mm.read_coils(plc)
            return self._read_bits_response(FC_READ_COILS, table, start, count)
        finally:
            self._release()

    def _read_discrete(self, pdu: bytes) -> bytes:
        plc = self._plc()
        try:
            start, count = self._read_bits_request(pdu)
            table = mm.read_discrete_inputs(plc)
            return self._read_bits_response(FC_READ_DISCRETE, table, start, count)
        finally:
            self._release()

    def _read_holding(self, pdu: bytes) -> bytes:
        plc = self._plc()
        try:
            start, count = self._read_registers_request(pdu)
            table = mm.read_holding_registers(plc)
            return self._read_registers_response(FC_READ_HOLDING, table, start, count)
        finally:
            self._release()

    def _read_input(self, pdu: bytes) -> bytes:
        plc = self._plc()
        try:
            start, count = self._read_registers_request(pdu)
            table = mm.read_input_registers(plc)
            return self._read_registers_response(FC_READ_INPUT, table, start, count)
        finally:
            self._release()

    # -- write handlers (routed through the PLC's validated setters) -----
    def _write_coil(self, pdu: bytes) -> bytes:
        # Coils (Q image) are read-only: the PLC, not a remote master, owns
        # the digital outputs.
        raise ModbusError(EX_ILLEGAL_ADDRESS)

    def _write_coils(self, pdu: bytes) -> bytes:
        raise ModbusError(EX_ILLEGAL_ADDRESS)

    def _write_register(self, pdu: bytes) -> bytes:
        if len(pdu) != 5:
            raise ModbusError(EX_ILLEGAL_VALUE)
        addr, value = struct.unpack(">Hh", pdu[1:5])
        plc = self._plc()
        try:
            accepted = mm.write_holding_register(plc, addr, value)
        except LookupError:
            raise ModbusError(EX_ILLEGAL_ADDRESS)
        except ValueError:
            raise ModbusError(EX_ILLEGAL_VALUE)
        finally:
            self._release()
        return bytes([FC_WRITE_REGISTER]) + struct.pack(">Hh", addr, accepted)

    def _write_registers(self, pdu: bytes) -> bytes:
        if len(pdu) < 6:
            raise ModbusError(EX_ILLEGAL_VALUE)
        addr, count, bytecount = struct.unpack(">HHB", pdu[1:6])
        if bytecount != count * 2 or len(pdu) != 6 + bytecount:
            raise ModbusError(EX_ILLEGAL_VALUE)
        values = struct.unpack(f">{count}h", pdu[6:6 + bytecount])

        plc = self._plc()
        try:
            # Validate the whole range first so a bad address cannot leave a
            # half-applied write behind.
            for i in range(count):
                kind, _tag, _scale, writable = mm.HOLDING_REGISTERS[addr + i]
                if not writable:
                    raise ModbusError(EX_ILLEGAL_ADDRESS)
            for i, value in enumerate(values):
                mm.write_holding_register(plc, addr + i, value)
        except IndexError:
            raise ModbusError(EX_ILLEGAL_ADDRESS)
        except LookupError:
            raise ModbusError(EX_ILLEGAL_ADDRESS)
        except ValueError:
            raise ModbusError(EX_ILLEGAL_VALUE)
        finally:
            self._release()
        return bytes([FC_WRITE_REGISTERS]) + struct.pack(">HH", addr, count)

    # -- request/response helpers -----------------------------------------
    @staticmethod
    def _read_bits_request(pdu: bytes) -> tuple[int, int]:
        if len(pdu) != 5:
            raise ModbusError(EX_ILLEGAL_VALUE)
        start, count = struct.unpack(">HH", pdu[1:5])
        if count < 1 or count > _MAX_BITS:
            raise ModbusError(EX_ILLEGAL_VALUE)
        return start, count

    @staticmethod
    def _read_registers_request(pdu: bytes) -> tuple[int, int]:
        if len(pdu) != 5:
            raise ModbusError(EX_ILLEGAL_VALUE)
        start, count = struct.unpack(">HH", pdu[1:5])
        if count < 1 or count > _MAX_REGISTERS:
            raise ModbusError(EX_ILLEGAL_VALUE)
        return start, count

    @staticmethod
    def _read_bits_response(fc: int, table: list[bool], start: int,
                            count: int) -> bytes:
        if start < 0 or start + count > len(table):
            raise ModbusError(EX_ILLEGAL_ADDRESS)
        payload = _pack_bits(table[start:start + count])
        return bytes([fc, len(payload)]) + payload

    @staticmethod
    def _read_registers_response(fc: int, table: list[int], start: int,
                                 count: int) -> bytes:
        if start < 0 or start + count > len(table):
            raise ModbusError(EX_ILLEGAL_ADDRESS)
        out = bytearray()
        for value in table[start:start + count]:
            out += struct.pack(">h", value)
        return bytes([fc, len(out)]) + bytes(out)


def _make_handler(server: ModbusServer):
    """Build a BaseRequestHandler bound to a ModbusServer instance."""

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            sock: socket.socket = self.request
            client_ip = self.client_address[0]
            if not server._client_allowed(client_ip):
                logger.warning("Modbus connection from %s rejected "
                                "(not in allowlist)", client_ip)
                return
            sock.settimeout(10.0)
            self.sock = sock
            while True:
                try:
                    header = self._recv_exact(7)
                except (socket.timeout, ConnectionError, OSError):
                    return
                if len(header) < 7:
                    return
                txid, proto, length, unit = MBAP.unpack(header)
                if length < 2 or length > 260:
                    return  # malformed length; drop the connection
                body = self._recv_exact(length - 1)  # PDU after the unit id
                try:
                    resp_pdu = server.handle_request(txid, unit, body)
                    frame = MBAP.pack(txid, proto, 1 + len(resp_pdu), unit) + resp_pdu
                except ModbusError as exc:
                    frame = _exception_frame(txid, proto, unit, body, exc.code)
                self.request.sendall(frame)

        def _recv_exact(self, n: int) -> bytes:
            chunks = []
            remaining = n
            while remaining > 0:
                chunk = self.sock.recv(remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)

    return Handler


def _exception_frame(txid: int, proto: int, unit: int, pdu: bytes,
                     code: int) -> bytes:
    fc = pdu[0] if pdu else 0
    resp = bytes([fc | 0x80, code])
    return MBAP.pack(txid, proto, 1 + len(resp), unit) + resp
