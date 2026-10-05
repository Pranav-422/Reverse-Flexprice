"""SQLite connection management.

WAL mode plus a busy timeout lets concurrent ingestion threads contend on the
UNIQUE index instead of erroring out with "database is locked"
(ARCHITECTURE.md section 4).
"""
import os
import sqlite3
import threading

from app.core import config
from app.db.models import CYCLE_UNIQUE_INDEX, SCHEMA

_local = threading.local()


def reset_connection_cache() -> None:
    """Drop any cached handle (used by tests between isolated databases)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    _local.conn = None
    _local.path = None


def get_connection() -> sqlite3.Connection:
    path = config.database_path()
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    if conn is not None:
        reset_connection_cache()

    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)

    conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    _local.conn = conn
    _local.path = path
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.executescript(CYCLE_UNIQUE_INDEX)


def fetch_one(sql: str, params=()):
    return get_connection().execute(sql, params).fetchone()


def fetch_all(sql: str, params=()):
    return get_connection().execute(sql, params).fetchall()


def execute(sql: str, params=()):
    return get_connection().execute(sql, params)
