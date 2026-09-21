"""Deterministic Session Summary reducer."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from offerpilot.database.connection import get_db
from offerpilot.database.values import dumps, now
from offerpilot.profiles.growth import rebuild_profile_growth


def get_session_summary(session_id: str) -> dict[str, Any] | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT session_id, summary_version, source_message_id, summary_json, updated_at "
            "FROM session_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "summary_version": row["summary_version"],
            "source_message_id": row["source_message_id"],
            "summary_json": json.loads(row["summary_json"] or "{}"),
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def rebuild_session_summary(session_id: str, profile_id: str) -> dict[str, Any]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT p.exam_point_id, p.status, p.evidence, r.question, r.created_at, r.id AS report_id "
            "FROM diagnosis_point_results p JOIN diagnosis_reports r ON r.id = p.report_id "
            "WHERE r.session_id = ? AND r.profile_id = ? ORDER BY r.created_at ASC",
            (session_id, profile_id),
        ).fetchall()
        grouped: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            grouped[row["exam_point_id"]].append(row)
        recurring = [
            point_id
            for point_id, items in grouped.items()
            if sum(item["status"] in {"partial", "missing"} for item in items) >= 2
        ]
        mastered = [
            point_id
            for point_id, items in grouped.items()
            if len(items) >= 2 and items[-1]["status"] == "covered" and items[-2]["status"] == "covered"
        ]
        session = conn.execute(
            "SELECT title, title_source FROM sessions WHERE id = ? AND profile_id = ?", (session_id, profile_id)
        ).fetchone()
        latest = conn.execute(
            "SELECT id, question, overall_score, created_at FROM diagnosis_reports "
            "WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        followups = conn.execute(
            "SELECT id FROM session_followups WHERE session_id = ? AND status <> 'answered' "
            "ORDER BY created_at ASC LIMIT 5",
            (session_id,),
        ).fetchall()
        message = conn.execute(
            "SELECT id FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)
        ).fetchone()
        auto_title = " ".join(str(latest["question"]).split())[:120] if latest else ""
        timestamp = now()
        if session and session["title_source"] == "auto" and auto_title:
            conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ? AND profile_id = ?",
                (auto_title, timestamp, session_id, profile_id),
            )
        summary = {
            "current_goal": session["title"] if session and session["title_source"] == "manual" and session["title"] else auto_title,
            "recurring_weaknesses": recurring[:30],
            "mastered_topics": mastered[:30],
            "pending_followup_ids": [row["id"] for row in followups],
            "latest_diagnosis": dict(latest) if latest else None,
        }
        old = conn.execute(
            "SELECT summary_version FROM session_summaries WHERE session_id = ?", (session_id,)
        ).fetchone()
        version = int(old["summary_version"] if old else 0) + 1
        conn.execute(
            "INSERT INTO session_summaries(session_id, summary_version, source_message_id, summary_json, updated_at) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET summary_version=excluded.summary_version, "
            "source_message_id=excluded.source_message_id, summary_json=excluded.summary_json, updated_at=excluded.updated_at",
            (session_id, version, message["id"] if message else None, dumps(summary), timestamp),
        )
        conn.commit()
    finally:
        conn.close()
    rebuild_profile_growth(profile_id)
    return {
        "session_id": session_id,
        "summary_version": version,
        "source_message_id": message["id"] if message else None,
        "summary_json": summary,
        "updated_at": timestamp,
    }
