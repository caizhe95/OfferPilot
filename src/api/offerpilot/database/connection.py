"""Small SQLite connection and schema initialization helpers."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from offerpilot.core.config import settings


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def get_db() -> sqlite3.Connection:
    """Open a SQLite connection with the settings needed by the app."""
    conn = sqlite3.connect(str(settings.db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db() -> None:
    """Create the current Demo schema when the application starts."""
    conn = get_db()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
