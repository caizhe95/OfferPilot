"""Approved Profile memories with durable, attributable sources."""

from __future__ import annotations

from typing import Any

from offerpilot.database.connection import get_db
from offerpilot.database.values import now


def get_memories(profile_id: str, key: str | None = None) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        sql = (
            "SELECT id, source_session_id AS session_id, source_run_id AS run_id, "
            "source_report_id AS report_id, profile_id, key, value, category, created_at "
            "FROM profile_memories WHERE profile_id = ?"
        )
        params: list[Any] = [profile_id]
        if key:
            sql += " AND key = ?"
            params.append(key)
        return [dict(row) for row in conn.execute(sql + " ORDER BY created_at DESC", params).fetchall()]
    finally:
        conn.close()


def save_memory(
    *,
    session_id: str,
    profile_id: str,
    key: str,
    value: str,
    category: str,
    run_id: str | None = None,
    report_id: str | None = None,
) -> dict[str, Any]:
    if not value.strip():
        raise ValueError("invalid_memory")
    if not run_id:
        raise RuntimeError("memory_write_not_allowed")
    timestamp = now()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        approval_id = None
        row = conn.execute(
            "SELECT a.id FROM approvals a JOIN runs r ON r.id = a.run_id "
            "WHERE a.run_id = ? AND a.session_id = ? AND a.profile_id = ? "
            "AND a.tool_name = 'save_memory' AND a.flow_kind = 'coach' "
            "AND a.status = 'executing' AND r.status = 'running' "
            "AND r.cancel_requested_at = '' ORDER BY a.created_at DESC LIMIT 1",
            (run_id, session_id, profile_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("memory_write_not_allowed")
        approval_id = str(row["id"])
        if approval_id:
            existing = conn.execute(
                "SELECT * FROM profile_memories WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if existing:
                return {
                    "id": existing["id"],
                    "session_id": existing["source_session_id"],
                    "profile_id": existing["profile_id"],
                    "key": existing["key"],
                    "value": existing["value"],
                    "category": existing["category"],
                    "created_at": existing["created_at"],
                }
        cursor = conn.execute(
            "INSERT INTO profile_memories(profile_id, source_session_id, source_run_id, "
            "source_report_id, approval_id, key, value, category, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (profile_id, session_id, run_id, report_id, approval_id, key, value[:300], category, timestamp),
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "session_id": session_id,
            "profile_id": profile_id,
            "key": key,
            "value": value[:300],
            "category": category,
            "created_at": timestamp,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
