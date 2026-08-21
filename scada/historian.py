"""SQLite-backed time-series historian for simulation trends.

The live charts in the HMI read the in-memory ``runtime.history`` deque; this
store is the durable complement.  The runtime flushes full-resolution samples
here periodically so trends survive a server restart and can be queried by
time range through the REST API.

Storage is a single SQLite database written with the standard library only
(``sqlite3``), mirroring the JSON leak store's zero-extra-dependency design.
Rows are stored long-form (``ts, series, value``) so any process variable can
be added without a schema migration.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from . import config

logger = logging.getLogger("scada.historian")

# Column name reserved for the timestamp inside each history record.
TIME_KEY = "t"


def _default_path() -> Path:
    """Repository-root-relative path, overridable via ``SCADA_TREND_STORE``."""
    env = os.environ.get("SCADA_TREND_STORE")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / config.TREND_STORE_FILE


class TrendStore:
    """Thread-safe, file-backed time-series store.

    A corrupt database is recreated empty rather than raising, so persistence
    can never break the simulation."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else _default_path()
        self._lock = threading.Lock()
        self._inserts = 0   # rows inserted since the last retention prune
        self._conn = self._open()

    def _open(self) -> sqlite3.Connection:
        """Open the store, quarantining (not silently discarding) a corrupt
        database: the broken file is renamed with a ``.corrupt-<ts>`` suffix
        and an ERROR is logged, then an empty store is created."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return self._connect()
        except sqlite3.DatabaseError:
            logger.error("trend store corrupt at %s; quarantining and "
                         "recreating empty", self.path, exc_info=True)
            try:
                self.path.replace(self.path.with_suffix(
                    self.path.suffix + f".corrupt-{int(time.time())}"))
            except OSError:
                pass
            return self._connect()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS samples ("
                " ts REAL NOT NULL,"
                " series TEXT NOT NULL,"
                " value REAL NOT NULL)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_samples_series_ts "
                "ON samples (series, ts)")
            # Durable alarm journal (raised/acked/cleared transitions).
            conn.execute(
                "CREATE TABLE IF NOT EXISTS alarm_events ("
                " tag TEXT NOT NULL,"
                " message TEXT NOT NULL,"
                " priority TEXT NOT NULL,"
                " action TEXT NOT NULL,"
                " t REAL NOT NULL,"
                " wall REAL NOT NULL)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alarm_events_t "
                "ON alarm_events (t)")
            conn.commit()
            return conn
        except sqlite3.DatabaseError:
            # Release the handle so the corrupt file can be renamed away.
            try:
                conn.close()
            except Exception:
                pass
            raise

    # -- writes -----------------------------------------------------------
    def insert(self, records: list[dict]) -> int:
        """Persist one or more history records (each has a 't' key plus one or
        more series keys).  Returns the number of rows written."""
        rows: list[tuple[float, str, float]] = []
        for rec in records:
            ts = rec.get(TIME_KEY)
            if ts is None:
                continue
            for series, value in rec.items():
                if series == TIME_KEY or not isinstance(value, (int, float)):
                    continue
                rows.append((float(ts), series, float(value)))
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO samples (ts, series, value) VALUES (?, ?, ?)", rows)
            self._conn.commit()
            self._prune_if_needed(len(rows))
        return len(rows)

    def _prune_if_needed(self, inserted: int) -> None:
        """Enforce the retention cap (oldest rows pruned by insertion order)
        roughly once per 10k inserted samples, keeping the store bounded."""
        self._inserts += inserted
        if self._inserts < 10000:
            return
        self._inserts = 0
        max_rows = config.HISTORIAN_MAX_ROWS
        cur = self._conn.execute("SELECT MAX(rowid) FROM samples")
        total = cur.fetchone()[0] or 0
        overage = total - max_rows
        if overage > 0:
            self._conn.execute(
                "DELETE FROM samples WHERE rowid IN "
                "(SELECT rowid FROM samples ORDER BY rowid LIMIT ?)",
                (overage,))
            self._conn.commit()

    # -- alarm journal ----------------------------------------------------
    def record_alarm_event(self, tag: str, message: str, priority: str,
                           action: str, t: float) -> None:
        """Persist one alarm transition (RAISE / ACK / CLEAR)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO alarm_events (tag, message, priority, action, "
                "t, wall) VALUES (?, ?, ?, ?, ?, ?)",
                (tag, message, priority, action, float(t), time.time()))
            self._conn.commit()

    def alarm_history(self, limit: int = 200) -> list[dict]:
        """Most recent alarm transitions, newest first."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT tag, message, priority, action, t, wall "
                "FROM alarm_events ORDER BY t DESC, wall DESC LIMIT ?",
                (max(1, int(limit)),))
            return [{"tag": r[0], "message": r[1], "priority": r[2],
                     "action": r[3], "t": r[4], "wall": r[5]}
                    for r in cur.fetchall()]

    # -- reads ------------------------------------------------------------
    def series(self) -> list[str]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT series FROM samples ORDER BY series")
            return [r[0] for r in cur.fetchall()]

    def time_range(self, series: str) -> tuple[float, float] | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT MIN(ts), MAX(ts) FROM samples WHERE series = ?",
                (series,))
            row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0]), float(row[1])

    def range(self, series: str, t0: float, t1: float,
              max_points: int = 400) -> list[tuple[float, float]]:
        """Downsampled (ts, value) pairs for one series over [t0, t1]."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts, value FROM samples "
                "WHERE series = ? AND ts >= ? AND ts <= ? ORDER BY ts",
                (series, float(t0), float(t1)))
            rows = cur.fetchall()
        if len(rows) <= max_points:
            return [(float(t), float(v)) for t, v in rows]
        step = len(rows) / max_points
        idx = [int(i * step) for i in range(max_points)]
        return [(float(rows[i][0]), float(rows[i][1])) for i in idx]

    def latest(self, series: str) -> tuple[float, float] | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT ts, value FROM samples WHERE series = ? "
                "ORDER BY ts DESC LIMIT 1", (series,))
            row = cur.fetchone()
        return (float(row[0]), float(row[1])) if row else None

    def count(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM samples")
            return int(cur.fetchone()[0])

    def clear(self) -> None:
        """Drop all rows (used by tests; not exposed to the UI)."""
        with self._lock:
            self._conn.execute("DELETE FROM samples")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
