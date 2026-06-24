from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path

# Re-entrant so event emits can nest inside an already-held write section on the same thread.
write_lock = threading.RLock()


class _BufferedCursor:
    """A query's rows fetched eagerly (inside the connection lock) so a caller can iterate
    or fetchone()/fetchall() after the lock is released without racing other threads on the
    shared connection. Mirrors the small slice of the sqlite3.Cursor API the orchestrator uses.
    """

    __slots__ = ("_rows", "_idx", "lastrowid", "rowcount")

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self.lastrowid = cursor.lastrowid
        self.rowcount = cursor.rowcount
        try:
            self._rows: list[sqlite3.Row] = cursor.fetchall()
        except sqlite3.Error:
            self._rows = []  # non-row statement (INSERT/UPDATE/DDL)
        self._idx = 0

    def fetchone(self) -> sqlite3.Row | None:
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return row
        return None

    def fetchall(self) -> list[sqlite3.Row]:
        rest = self._rows[self._idx:]
        self._idx = len(self._rows)
        return rest

    def __iter__(self) -> Iterator[sqlite3.Row]:
        start, self._idx = self._idx, len(self._rows)
        return iter(self._rows[start:])


class SerializedConnection:
    """Thread-safe proxy over one sqlite3 connection.

    A connection opened with ``check_same_thread=False`` may be SHARED across threads but is
    NOT safe for CONCURRENT use: simultaneous ``execute()`` calls from the FastAPI threadpool
    (e.g. several workers streaming usage heartbeats while others poll for jobs) corrupt the
    shared statement state and raise ``sqlite3.InterfaceError: bad parameter or other API
    misuse``. Route every operation through one re-entrant lock and buffer each query's rows
    inside that lock so ``execute()`` + fetch is atomic. ``write_lock`` is an ``RLock``, so the
    existing ``with write_lock:`` write sections that then call ``execute()`` re-acquire it on
    the same thread without deadlocking, and multi-statement sections stay atomic.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def execute(self, sql: str, parameters: Sequence = ()) -> _BufferedCursor:
        with self._lock:
            return _BufferedCursor(self._conn.execute(sql, parameters))

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __getattr__(self, name: str):
        # executescript, row_factory, etc. fall through to the underlying connection.
        return getattr(self._conn, name)


def init_db(db_path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    schema_path = Path(__file__).resolve().parents[1] / "contracts" / "schema.sql"
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def open_serialized_db(db_path: str = ":memory:") -> SerializedConnection:
    """Open the schema-initialized DB wrapped for safe concurrent use across threads."""
    return SerializedConnection(init_db(db_path), write_lock)


def connect(db_path: str = ":memory:") -> sqlite3.Connection:
    return init_db(db_path)
