from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

write_lock = threading.Lock()


def init_db(db_path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    schema_path = Path(__file__).resolve().parents[1] / "contracts" / "schema.sql"
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def connect(db_path: str = ":memory:") -> sqlite3.Connection:
    return init_db(db_path)
