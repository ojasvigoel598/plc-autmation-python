"""
Event-driven architecture for the SCADA system.

Separates COMMANDS (user intent) from EVENTS (what actually happened).
Every command has an ID for idempotency; events represent confirmed state
transitions or alarm changes.

Design:
    USER → COMMAND (with ID) → PLC VALIDATION → STATE TRANSITION → EVENT

Events are the authoritative record of what occurred, logged durably and
emitted to telemetry subscribers.  The UI must never become the source of
truth — it observes events, not creates them.

Command flow:
    1. User issues command via REST (POST /api/control/...)
    2. Server creates a CommandEnvelope with unique ID, timestamp, source IP
    3. PLC validates the command against permissives/interlocks
    4. If accepted: state changes, Event emitted, command marked EXECUTED
    5. If rejected: Event with reason emitted, command marked REJECTED
    6. Duplicate command IDs are detected and rejected (idempotency)

Event types:
    STATE_TRANSITION  — PLC state machine changed (IDLE→STARTING, etc.)
    ALARM_RAISE       — alarm condition detected
    ALARM_ACK         — operator acknowledged alarm
    ALARM_CLEAR       — condition resolved
    COMMAND_ACCEPTED  — command accepted and executed
    COMMAND_REJECTED  — command rejected (with reason)
    FAULT_INJECTED    — field fault injected (for testing/training)
    FAULT_CLEARED     — field fault cleared
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Literal


class EventType(str, Enum):
    STATE_TRANSITION = "STATE_TRANSITION"
    ALARM_RAISE = "ALARM_RAISE"
    ALARM_ACK = "ALARM_ACK"
    ALARM_CLEAR = "ALARM_CLEAR"
    COMMAND_ACCEPTED = "COMMAND_ACCEPTED"
    COMMAND_REJECTED = "COMMAND_REJECTED"
    FAULT_INJECTED = "FAULT_INJECTED"
    FAULT_CLEARED = "FAULT_CLEARED"


class CommandStatus(str, Enum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"


@dataclass
class CommandEnvelope:
    """Wrapper around a command with metadata for idempotency and audit."""
    command_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    source_ip: str = ""
    command_type: str = ""      # e.g. "SET_SETPOINT", "START", "E_STOP"
    target: str = ""            # e.g. "LIC-101", "P-101"
    parameters: dict = field(default_factory=dict)
    status: CommandStatus = CommandStatus.PENDING
    result: str = ""            # human-readable outcome


@dataclass
class Event:
    """An observable fact about the system — never a command."""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    wall_time: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()))
    event_type: EventType = EventType.STATE_TRANSITION
    source: str = ""            # component that emitted it
    severity: str = "INFO"      # INFO | WARNING | HIGH | CRITICAL
    tag: str = ""               # equipment tag
    message: str = ""
    details: dict = field(default_factory=dict)
    command_id: str | None = None  # linked command, if any


class EventStore:
    """Thread-safe, optionally persistent event store."""

    def __init__(self, path: Path | None = None, max_memory: int = 500) -> None:
        self.path = path
        self.max_memory = max_memory
        self._lock = threading.Lock()
        self._events: list[Event] = []
        self._command_log: dict[str, CommandEnvelope] = {}  # command_id → envelope
        if path:
            self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        ev = Event(**{k: v for k, v in data.items() if k in Event.__dataclass_fields__})
                        self._events.append(ev)
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for ev in self._events[-self.max_memory:]:
                    f.write(json.dumps(asdict(ev)) + "\n")
            tmp.replace(self.path)
        except OSError:
            pass

    def emit(self, event: Event) -> None:
        """Record an event and append to persistent store."""
        with self._lock:
            self._events.append(event)
            if len(self._events) > self.max_memory:
                self._events = self._events[-self.max_memory:]
            self._save()

    def check_duplicate(self, command_id: str) -> bool:
        """Returns True if this command_id was already processed."""
        with self._lock:
            return command_id in self._command_log

    def record_command(self, envelope: CommandEnvelope) -> None:
        """Log a command envelope for idempotency tracking."""
        with self._lock:
            self._command_log[envelope.command_id] = envelope
            # Keep memory bounded
            if len(self._command_log) > 1000:
                oldest = sorted(self._command_log.keys(),
                                key=lambda k: self._command_log[k].timestamp)[:500]
                for k in oldest:
                    del self._command_log[k]

    def recent(self, n: int = 50) -> list[Event]:
        with self._lock:
            return list(self._events[-n:])

    def by_type(self, event_type: EventType, n: int = 100) -> list[Event]:
        with self._lock:
            return [e for e in self._events if e.event_type == event_type][-n:]

    def summary(self) -> dict:
        """Quick summary for health/status endpoints."""
        with self._lock:
            counts = {}
            for e in self._events:
                counts[e.event_type.value] = counts.get(e.event_type.value, 0) + 1
            return {
                "total_events": len(self._events),
                "by_type": counts,
                "commands_pending": sum(1 for c in self._command_log.values()
                                       if c.status == CommandStatus.PENDING),
            }


def _default_event_path() -> Path:
    env = os.environ.get("SCADA_EVENT_STORE")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "events.jsonl"


# Module-level singleton (created lazily by the server lifespan).
_store: EventStore | None = None


def get_store() -> EventStore:
    global _store
    if _store is None:
        _store = EventStore(path=_default_event_path())
    return _store


def init_store(path: Path | None = None) -> EventStore:
    global _store
    _store = EventStore(path=path or _default_event_path())
    return _store
