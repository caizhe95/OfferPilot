"""Durable Session follow-up state transitions."""

from __future__ import annotations

import uuid
from typing import Any

from offerpilot.database.connection import get_db
from offerpilot.database.values import now


def create_followups(
    session_id: str, report_id: str, diagnosis: dict[str, Any], limit: int = 5
) -> list[dict[str, Any]]:
    followups = diagnosis.get("followups", []) if isinstance(diagnosis, dict) else []
    conn = get_db()
    result = []
    try:
        for item in followups[:limit]:
            question = str(item.get("question", "")).strip()
            if not question:
                continue
            timestamp = now()
            record = {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "source_report_id": report_id,
                "exam_point_id": item.get("exam_point_id"),
                "question": question[:1000],
                "reason": str(item.get("why", item.get("reason", "")))[:500],
                "status": "pending",
                "linked_run_id": None,
                "created_at": timestamp,
                "updated_at": timestamp,
                "answered_at": "",
            }
            conn.execute(
                "INSERT INTO session_followups(id, session_id, source_report_id, exam_point_id, "
                "question, reason, status, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    record["id"],
                    session_id,
                    report_id,
                    record["exam_point_id"],
                    record["question"],
                    record["reason"],
                    timestamp,
                    timestamp,
                ),
            )
            result.append(record)
        conn.commit()
    finally:
        conn.close()
    return result


def list_followups(session_id: str, profile_id: str) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT f.* FROM session_followups f JOIN sessions s ON s.id = f.session_id "
            "WHERE f.session_id = ? AND s.profile_id = ? ORDER BY f.created_at ASC",
            (session_id, profile_id),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def link_followup(followup_id: str, session_id: str, profile_id: str, run_id: str) -> bool:
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE session_followups SET status = 'in_progress', linked_run_id = ?, updated_at = ? "
            "WHERE id = ? AND session_id = ? AND status = 'pending' "
            "AND EXISTS(SELECT 1 FROM sessions WHERE id = ? AND profile_id = ?)",
            (run_id, now(), followup_id, session_id, session_id, profile_id),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def finish_followup_for_run(run_id: str, answered: bool) -> None:
    conn = get_db()
    try:
        timestamp = now()
        conn.execute(
            "UPDATE session_followups SET status = ?, answered_at = ?, updated_at = ? WHERE linked_run_id = ?",
            ("answered" if answered else "pending", timestamp if answered else "", timestamp, run_id),
        )
        conn.commit()
    finally:
        conn.close()
