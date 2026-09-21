"""SQLite persistence for anonymous Profile records."""

from __future__ import annotations

from offerpilot.database.connection import get_db
from offerpilot.database.values import now


def ensure_profile(profile_id: str) -> None:
    timestamp = now()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO profiles(id, created_at, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
            (profile_id, timestamp, timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def delete_profile_record(profile_id: str) -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        conn.commit()
    finally:
        conn.close()
