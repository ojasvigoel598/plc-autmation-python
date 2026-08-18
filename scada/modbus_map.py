"""Modbus register map for the PLC I/O image.

Maps the runtime's PLC I/O image (``scada.plc.PLC``) onto the standard
Modbus data model so a real SCADA, historian, or PLC client can attach over
Modbus TCP without any browser in the loop:

    coils            (FC 0x01 / 0x05 / 0x0F) : digital outputs Q  (read-only)
    discrete inputs  (FC 0x02)               : digital inputs I  (read-only)
    input registers  (FC 0x04)               : analog inputs AI  (read-only)
    holding regs     (FC 0x03 / 0x06 / 0x10) : analog outputs AQ (read-only)
                                              + operator data registers (write)

Addresses in this module are **zero-based offsets** within each data model;
``modbus_server`` adds the Modbus 1-based prefix per function code on the
wire.  Keeping the offsets here zero-based makes the tables positionally
obvious and easy to test.

Authority rule: the only writable holding registers are the *operator data
registers* the PLC already reads (setpoints, loop modes, manual outputs).
The PLC's own output image (AQ / Q) is read-only over the wire — a remote
master can never overwrite a PID or interlock decision.  Writes that would
bypass the PLC are rejected.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from . import config

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from .plc import PLC

# ---------------------------------------------------------------------------
# Scaling
# ---------------------------------------------------------------------------
# Modbus registers are signed 16-bit integers; analog engineering values are
# mapped to integer counts with a per-register scale so a client recovers the
# engineering unit by dividing (count / scale).
LEVEL_SCALE = 1000.0   # metres    -> counts (1 m == 1000 counts)
PCT_SCALE = 10.0       # percent   -> counts (1 % == 10 counts)
MODE_SCALE = 1.0       # enum      -> raw 0/1

# Sentinel for a non-finite (NaN/Inf) transmitter reading.  Over the wire a
# failed sensor must be distinguishable from a genuine zero, so it maps to
# 0x8000 — the conventional Modbus "invalid" marker.
INVALID_COUNT = -32768

# ---------------------------------------------------------------------------
# Digital tables
# ---------------------------------------------------------------------------
# Digital outputs (PLC "Q" coils) — read-only over Modbus.
COILS = ["Q0.0_PUMP", "Q0.1_HORN", "Q0.2_LIGHT", "Q0.3_STATUS"]

# Digital inputs (PLC "I" image) — read-only over Modbus.
DISCRETE_INPUTS = [
    "I0.0_ESTOP", "I0.1_START", "I0.2_STOP", "I0.3_RESET", "I0.4_PUMP_FB",
]

# ---------------------------------------------------------------------------
# Analog input registers (PLC "ai" image) — read-only.
# ---------------------------------------------------------------------------
# (tag, scale)
INPUT_REGISTERS = [
    ("LT-101", LEVEL_SCALE),
    ("LT-102", LEVEL_SCALE),
    ("LT-103", LEVEL_SCALE),
    ("XV-101_FB", PCT_SCALE),
    ("XV-102_FB", PCT_SCALE),
    ("XV-103_FB", PCT_SCALE),
]

# ---------------------------------------------------------------------------
# Holding registers.
# ---------------------------------------------------------------------------
# kind is one of:
#   "aq"           read-only analog output (PLC-computed actuator command)
#   "setpoint"     writable loop setpoint (m)
#   "mode"         writable loop mode enum (0=AUTO, 1=MANUAL)
#   "manual_mv"    writable manual output (%) for a loop
#   "manual_valve" writable manual valve position (%)
#   "manual_pump"  writable manual pump speed (%)
# (kind, tag, scale, writable)
HOLDING_REGISTERS = [
    ("aq", "P-101", PCT_SCALE, False),
    ("aq", "XV-101", PCT_SCALE, False),
    ("aq", "XV-102", PCT_SCALE, False),
    ("aq", "XV-103", PCT_SCALE, False),
    ("setpoint", "LIC-101", LEVEL_SCALE, True),
    ("setpoint", "LIC-102", LEVEL_SCALE, True),
    ("mode", "LIC-101", MODE_SCALE, True),
    ("mode", "LIC-102", MODE_SCALE, True),
    ("manual_mv", "LIC-101", PCT_SCALE, True),
    ("manual_mv", "LIC-102", PCT_SCALE, True),
    ("manual_valve", "XV-102", PCT_SCALE, True),
    ("manual_valve", "XV-103", PCT_SCALE, True),
    ("manual_pump", "P-101", PCT_SCALE, True),
]

# ---------------------------------------------------------------------------
# Count <-> engineering value conversion
# ---------------------------------------------------------------------------
def encode(value: float, scale: float) -> int:
    """Convert an engineering value to a signed 16-bit Modbus count.

    NaN/Inf transmitter readings map to INVALID_COUNT (0x8000) so a client
    can distinguish a failed sensor from a real reading.
    """
    if isinstance(value, bool):  # pragma: no cover - defensive
        value = float(value)
    if value is None or math.isnan(float(value)) or math.isinf(float(value)):
        return INVALID_COUNT
    return max(-32768, min(32767, round(float(value) * scale)))


def decode(count: int, scale: float) -> float:
    """Convert a signed 16-bit Modbus count back to engineering units."""
    if count == INVALID_COUNT:
        return float("nan")
    return count / scale


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


# ---------------------------------------------------------------------------
# Read views (pull the live I/O image from a PLC instance)
# ---------------------------------------------------------------------------
def read_coils(plc: "PLC") -> list[bool]:
    return [bool(plc.q[tag]) for tag in COILS]


def read_discrete_inputs(plc: "PLC") -> list[bool]:
    return [bool(plc.i[tag]) for tag in DISCRETE_INPUTS]


def read_input_registers(plc: "PLC") -> list[int]:
    return [encode(plc.ai[tag], scale) for tag, scale in INPUT_REGISTERS]


def _read_holding_one(plc: "PLC", kind: str, tag: str, scale: float) -> int:
    if kind == "aq":
        return encode(plc.aq[tag], scale)
    if kind == "setpoint":
        return encode(plc.pids[tag].setpoint, scale)
    if kind == "mode":
        return 0 if plc.loop_mode[tag] == "AUTO" else 1
    if kind == "manual_mv":
        return encode(plc.loop_manual_mv[tag], scale)
    if kind == "manual_valve":
        return encode(plc.manual_valves[tag], scale)
    if kind == "manual_pump":
        return encode(plc.manual_pump, scale)
    raise KeyError(f"unknown holding register kind: {kind}")


def read_holding_registers(plc: "PLC") -> list[int]:
    return [_read_holding_one(plc, kind, tag, scale)
            for kind, tag, scale, _ in HOLDING_REGISTERS]


# ---------------------------------------------------------------------------
# Write routing (writes go through the PLC's validated setters, never into
# the output image directly).
# ---------------------------------------------------------------------------
def write_holding_register(plc: "PLC", offset: int, count: int) -> int:
    """Apply one holding-register write through the PLC command interface.

    Returns the count that was actually accepted (post-clamping), so the
    Modbus response reflects the truth.  Raises LookupError for read-only or
    out-of-range addresses — the server maps that to an exception response.
    """
    if not 0 <= offset < len(HOLDING_REGISTERS):
        raise LookupError(f"holding register {offset} out of range")
    kind, tag, scale, writable = HOLDING_REGISTERS[offset]
    if not writable:
        raise LookupError(f"holding register {offset} ({tag}) is read-only")

    if kind == "setpoint":
        value = _clamp(decode(count, scale), 0.0, config.TANK_HEIGHT)
        plc.set_setpoint(tag, value)
        return encode(value, scale)
    if kind == "mode":
        mode = "AUTO" if count == 0 else ("MANUAL" if count == 1 else None)
        if mode is None:
            raise ValueError(f"invalid loop mode code {count} for {tag}")
        plc.set_loop_mode(tag, mode)
        return 0 if mode == "AUTO" else 1
    if kind == "manual_mv":
        value = _clamp(decode(count, scale), 0.0, 100.0)
        # Keep the loop's current mode; only the manual output changes.
        plc.set_loop_mode(tag, plc.loop_mode[tag], value)
        return encode(value, scale)
    if kind == "manual_valve":
        value = _clamp(decode(count, scale), 0.0, 100.0)
        plc.set_manual_valve(tag, value)
        return encode(value, scale)
    if kind == "manual_pump":
        value = _clamp(decode(count, scale), 0.0, 100.0)
        plc.set_manual_pump(value)
        return encode(value, scale)
    raise KeyError(f"unknown holding register kind: {kind}")
