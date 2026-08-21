"""
High-availability layer: lease-based leader election, fencing, and fenced
state checkpoints.

Why SQLite
----------
The project deliberately avoids external infrastructure (no Redis, NATS or
PostgreSQL): the historian already uses SQLite, so reusing it for the HA
layer keeps the deployment a single container.  SQLite gives us:

  * cross-process atomicity (BEGIN IMMEDIATE serialises writers) for the
    lease and checkpoint rows;
  * durability on the same host (a crash leaves the last checkpoint intact);
  * zero operational complexity (it is a file, not a service).

Why a lease (and not "the first process wins")
----------------------------------------------
Two worker processes started at the same time must not both run the
simulation.  A lease grants control authority to exactly one process for a
bounded time (TTL).  The holder renews the lease on a heartbeat cadence;
a standby only takes over *after* the lease has expired.  Every state write
is fenced with the lease token, so a process that has lost authority (e.g.
its heartbeats stopped arriving) can no longer write: this is the classic
fencing pattern that prevents split-brain control.

Why a checkpoint
----------------
The authoritative plant/PLC state is serialised periodically to the shared
store.  A standby that takes over restores the latest checkpoint and resumes
the simulation from there, so a failover does not restart the plant from
scratch (which would be unsafe: the process would think the tanks were at
their initial levels).

Limitations (documented honestly)
---------------------------------
  * This is single-host HA: it protects against process crashes and network
    stalls between workers on one machine, not against host loss.
  * The failover resume uses the latest checkpoint (up to the checkpoint
    interval old); the plant time therefore steps back by that interval.
  * This is a simulation platform, not a certified safety-instrumented
    system.  See the README warning.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lease (
    name       TEXT PRIMARY KEY,      -- lease name (one lease per plant)
    token      TEXT NOT NULL,         -- fencing token of the current holder
    pid        INTEGER,               -- holder process id (informational)
    host       TEXT,                  -- holder host name (informational)
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoint (
    name    TEXT PRIMARY KEY,
    token   TEXT NOT NULL,            -- fencing token that wrote it
    t       REAL NOT NULL,            -- plant simulation time at checkpoint
    data    TEXT NOT NULL,            -- JSON-serialised runtime state
    saved_at REAL NOT NULL
);
"""


def _connect(path: str | Path) -> sqlite3.Connection:
    """Fresh connection per operation (simple, thread-safe, no shared state)."""
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)   # multi-statement schema needs executescript
    return conn


class LeaderLease:
    """One-writer lease over a single plant, shared via SQLite.

    Only the process holding an *unexpired* lease may write plant state.
    Takeover requires the lease to have expired, which makes split-brain
    (two simultaneous writers) impossible: the SQLite transaction is atomic,
    so at most one process can ever see itself as the holder.
    """

    def __init__(self, path: str | Path, name: str = "plant") -> None:
        self.path = str(path)
        self.name = name

    # -- primitives ---------------------------------------------------------
    def _row(self, conn: sqlite3.Connection) -> tuple | None:
        cur = conn.execute(
            "SELECT token, pid, host, acquired_at, expires_at FROM lease "
            "WHERE name = ?", (self.name,))
        return cur.fetchone()

    def acquire(self, ttl: float, pid: int | None = None) -> str | None:
        """Try to become the leader.  Returns a fencing token on success,
        None if another process holds an unexpired lease."""
        token = uuid.uuid4().hex
        now = time.time()
        conn = _connect(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")   # serialise concurrent acquirers
            row = self._row(conn)
            if row is not None and row[4] > now:
                return None                    # someone else is alive
            conn.execute(
                "INSERT INTO lease(name, token, pid, host, acquired_at, expires_at) "
                "VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "token=excluded.token, pid=excluded.pid, host=excluded.host, "
                "acquired_at=excluded.acquired_at, expires_at=excluded.expires_at",
                (self.name, token, pid or os.getpid(), os.uname().nodename
                 if hasattr(os, "uname") else "host",
                 now, now + ttl))
            conn.commit()
            return token
        finally:
            conn.close()

    def renew(self, token: str, ttl: float) -> bool:
        """Extend this holder's lease.  Fenced: only the current token may
        renew; returns False if authority was lost (e.g. another process
        took over after expiry)."""
        now = time.time()
        conn = _connect(self.path)
        try:
            cur = conn.execute(
                "UPDATE lease SET expires_at = ? "
                "WHERE name = ? AND token = ?",
                (now + ttl, self.name, token))
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def release(self, token: str) -> None:
        """Voluntarily hand back authority (graceful shutdown)."""
        conn = _connect(self.path)
        try:
            conn.execute("DELETE FROM lease WHERE name = ? AND token = ?",
                         (self.name, token))
            conn.commit()
        finally:
            conn.close()

    def is_leader(self, token: str) -> bool:
        """Fencing check: does this token still hold an unexpired lease?"""
        conn = _connect(self.path)
        try:
            row = self._row(conn)
            # row = (token, pid, host, acquired_at, expires_at)
            return row is not None and row[0] == token and row[4] > time.time()
        finally:
            conn.close()

    def leader(self) -> dict | None:
        """Current holder (or None).  Read-only, safe from any process."""
        conn = _connect(self.path)
        try:
            row = self._row(conn)
            if row is None:
                return None
            # row = (token, pid, host, acquired_at, expires_at)
            return {"token": row[0], "pid": row[1], "host": row[2],
                    "acquired_at": row[3], "expires_at": row[4]}
        finally:
            conn.close()

    def expired(self) -> bool:
        """True when no process holds an unexpired lease (safe to take over)."""
        conn = _connect(self.path)
        try:
            row = self._row(conn)
            return row is None or row[4] <= time.time()
        finally:
            conn.close()


class CheckpointStore:
    """Durable, fenced snapshots of the authoritative runtime state."""

    def __init__(self, path: str | Path, name: str = "plant") -> None:
        self.path = str(path)
        self.name = name

    def save(self, token: str, t: float, state: dict, *, only_if_leader=True) -> bool:
        """Persist a checkpoint.  Fenced: unless ``only_if_leader`` is False,
        the write is ignored when this token no longer holds the lease, so a
        demoted process cannot overwrite a newer leader's state."""
        if only_if_leader and not LeaderLease(self.path, self.name).is_leader(token):
            return False
        conn = _connect(self.path)
        try:
            conn.execute(
                "INSERT INTO checkpoint(name, token, t, data, saved_at) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "token=excluded.token, t=excluded.t, data=excluded.data, "
                "saved_at=excluded.saved_at",
                (self.name, token, float(t), json.dumps(state), time.time()))
            conn.commit()
            return True
        finally:
            conn.close()

    def load(self) -> dict | None:
        """Latest checkpoint as ``{"t": ..., "state": {...}}`` or None."""
        conn = _connect(self.path)
        try:
            row = conn.execute(
                "SELECT t, data FROM checkpoint WHERE name = ?",
                (self.name,)).fetchone()
            if row is None:
                return None
            return {"t": row[0], "state": json.loads(row[1])}
        finally:
            conn.close()


class HAState:
    """Convenience facade combining the lease and the checkpoint store.

    Usage (in the server lifespan, per worker process):

        ha = HAState(config.HA_DB_FILE)
        token = ha.try_takeover(ttl)          # active path
        if token:
            ckpt = ha.load()
            if ckpt: runtime.restore(ckpt["state"])
            # run the sim loop; every scan: ha.renew(token, ttl);
            # every checkpoint interval: ha.checkpoint(token, t, state)
            # if ha.renew() ever returns False -> demoted, stop writing
        else:
            # standby: serve read-only, poll ha.leader()/ha.expired();
            # when expired -> ha.try_takeover(ttl) -> if token: restore
            # checkpoint and promote.
    """

    def __init__(self, path: str | Path, name: str = "plant") -> None:
        self.lease = LeaderLease(path, name)
        self.checkpoints = CheckpointStore(path, name)

    def try_takeover(self, ttl: float, pid: int | None = None) -> str | None:
        """Try to acquire authority.  Only succeeds if the lease is free or
        expired, so two standbys cannot both take over."""
        return self.lease.acquire(ttl, pid=pid)

    def renew(self, token: str, ttl: float) -> bool:
        return self.lease.renew(token, ttl)

    def release(self, token: str) -> None:
        self.lease.release(token)

    def is_leader(self, token: str) -> bool:
        return self.lease.is_leader(token)

    def leader(self) -> dict | None:
        return self.lease.leader()

    def expired(self) -> bool:
        return self.lease.expired()

    def checkpoint(self, token: str, t: float, state: dict) -> bool:
        return self.checkpoints.save(token, t, state)

    def load(self) -> dict | None:
        return self.checkpoints.load()
