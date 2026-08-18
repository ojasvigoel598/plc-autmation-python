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

import os
import sqlite3
import threading
from pathlib import Path

from . import config

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
        self._conn = self._open()

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS samples ("
            " ts REAL NOT NULL,"
            " series TEXT NOT NULL,"
            " value REAL NOT NULL)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_series_ts "
            "ON samples (series, ts)")
        conn.commit()
        return conn

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
        return len(rows)

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
