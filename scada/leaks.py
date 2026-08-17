"""
Persistent leak-event store.

Leak events are produced by the runtime's mass-balance detector (see
`runtime.py`) and recorded here so the investigation history survives server
restarts and browser refreshes without a full database.  Storage is a single
JSON file written atomically; the latest 30 events are always readily
available via `recent()`.

Only *detected* tank leaks are recorded.  This module holds no physics and no
control logic — it is purely the event log.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config

# Repository-root-relative path so the store is stable regardless of CWD.
STORE_PATH = Path(__file__).resolve().parent.parent / config.LEAK_STORE_FILE


def severity_for(rate_m3s: float) -> str:
    """Classify a leak rate (m^3/s).  Thresholds are relative to the feed pump's
    maximum delivery so severity scales with plant size."""
    if rate_m3s >= 0.01:        # >= 10 L/s
        return "HIGH"
    if rate_m3s >= 0.002:       # >= 2 L/s
        return "MODERATE"
    return "LOW"


@dataclass
class LeakEvent:
    """One detected leak.  `rate_m3s` is the *estimated* unexplained loss; the
    other fields are measured facts captured at detection/resolution time."""
    id: str
    tank: str                 # affected tank tag (leaks only occur at tanks)
    location: str             # "TK-101 (tank)"
    rate_m3s: float           # estimated leak rate, m^3/s
    status: str               # "ACTIVE" | "RESOLVED"
    severity: str             # LOW | MODERATE | HIGH
    t_start: float            # sim time at detection (s)
    t_end: float | None       # sim time at resolution, None while active
    level_before: float       # tank level (m) at detection
    source: str               # detection method, e.g. "mass-balance"
    alarm_tag: str            # alarm tag raised for this leak ("" if none)
    wall_start: float = field(default_factory=time.time)
    wall_end: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class LeakStore:
    """Thread-safe, file-backed event log.  Corrupt/missing files degrade to an
    empty store rather than raising, so persistence can never break the sim."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else STORE_PATH
        self._lock = threading.Lock()
        self._events: list[LeakEvent] = []
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._events = [
                    LeakEvent(**e) for e in data.get("events", [])
                    if isinstance(e, dict) and "id" in e
                ]
        except (OSError, json.JSONDecodeError, TypeError):
            self._events = []  # start fresh rather than crash the sim

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            payload = {"events": [e.as_dict() for e in self._events]}
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.path)  # atomic on the same filesystem
        except OSError:
            pass  # persistence is best-effort; the in-memory log still works

    # -- event lifecycle --------------------------------------------------
    def record(self, *, tank: str, rate_m3s: float, level_before: float,
               source: str, alarm_tag: str = "", t_start: float = 0.0) -> LeakEvent:
        with self._lock:
            ev = LeakEvent(
                id=uuid.uuid4().hex[:12],
                tank=tank,
                location=f"{tank} (tank)",
                rate_m3s=round(float(rate_m3s), 6),
                status="ACTIVE",
                severity=severity_for(rate_m3s),
                t_start=float(t_start),
                t_end=None,
                level_before=float(level_before),
                source=source,
                alarm_tag=alarm_tag,
            )
            self._events.append(ev)
            self._save()
            return ev

    def resolve(self, leak_id: str, t_end: float) -> bool:
        with self._lock:
            for ev in self._events:
                if ev.id == leak_id and ev.status == "ACTIVE":
                    ev.status = "RESOLVED"
                    ev.t_end = float(t_end)
                    ev.wall_end = time.time()
                    self._save()
                    return True
            return False

    def active(self) -> list[LeakEvent]:
        with self._lock:
            return [e for e in self._events if e.status == "ACTIVE"]

    # -- queries ----------------------------------------------------------
    def latest(self) -> LeakEvent | None:
        with self._lock:
            return self._events[-1] if self._events else None

    def recent(self, n: int = config.LEAK_HISTORY_LIMIT) -> list[LeakEvent]:
        with self._lock:
            return list(reversed(self._events[-n:]))

    def get(self, leak_id: str) -> LeakEvent | None:
        with self._lock:
            for ev in self._events:
                if ev.id == leak_id:
                    return ev
            return None

    def count(self) -> int:
        with self._lock:
            return len(self._events)

    def clear(self) -> None:
        """Drop all history (used by tests; not exposed to the UI)."""
        with self._lock:
            self._events = []
            self._save()
