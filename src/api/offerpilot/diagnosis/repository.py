"""SQLite persistence for diagnosis reports and approved practice memories."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from offerpilot.core.errors import AppError
from offerpilot.database.connection import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def save_report(*, run_id: str, session_id: str, profile_id: str, question: str, answer: str, diagnosis: dict[str, Any], markdown: str, overall_score: float, knowledge: list[dict[str, Any]]) -> dict[str, Any]:
    report_id, timestamp = str(uuid.uuid4()), _now()
    sources = [str(item.get("source", item.get("title", "")))[:300] for item in knowledge]
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT id FROM runs WHERE id = ? AND session_id = ? AND profile_id = ? "
            "AND status = 'running' AND cancel_requested_at = ''",
            (run_id, session_id, profile_id),
        ).fetchone()
        if active is None:
            raise AppError("Diagnosis run is no longer active", code="diagnosis_not_active", status_code=409)
        conn.execute("INSERT INTO diagnosis_reports(id, run_id, session_id, profile_id, question, answer, content_scores, voice_scores, overall_score, report_markdown, diagnosis_json, sources, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (report_id, run_id, session_id, profile_id, question, answer, _json(diagnosis["content_scores"]), _json(diagnosis["voice_scores"]), overall_score, markdown, _json(diagnosis), _json(sources), timestamp))
        message = conn.execute("INSERT INTO messages(session_id, profile_id, run_id, role, kind, content, metadata, created_at) VALUES(?, ?, ?, 'assistant', 'diagnosis_report', ?, ?, ?)", (session_id, profile_id, run_id, markdown, _json({"report_id": report_id}), timestamp))
        for point in diagnosis.get("exam_points", []):
            conn.execute("INSERT INTO diagnosis_point_results(id, report_id, profile_id, exam_point_id, status, evidence, explanation, source_knowledge_id, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(uuid.uuid4()), report_id, profile_id, str(point["point_id"]), point.get("status", "missing"), point.get("evidence"), point.get("explanation", ""), point.get("knowledge_id"), timestamp))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"id": report_id, "report_id": report_id, "session_id": session_id, "overall_score": overall_score, "report": markdown, "diagnosis": diagnosis, "followups": diagnosis.get("followups", []), "sources": sources, "created_at": timestamp}


def get_report(report_id: str, profile_id: str | None = None) -> dict[str, Any] | None:
    conn = get_db()
    try:
        sql, params = "SELECT * FROM diagnosis_reports WHERE id = ?", [report_id]
        if profile_id: sql, params = sql + " AND profile_id = ?", [report_id, profile_id]
        row = conn.execute(sql, params).fetchone()
        if row is None: return None
        result = dict(row)
        result.update({"content_scores": json.loads(row["content_scores"] or "{}"), "voice_scores": json.loads(row["voice_scores"] or "{}"), "diagnosis": json.loads(row["diagnosis_json"] or "{}"), "sources": json.loads(row["sources"] or "[]")})
        return result
    finally: conn.close()


def list_reports(session_id: str, profile_id: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = get_db()
    try: return [dict(row) for row in conn.execute("SELECT id, run_id, question, overall_score, created_at FROM diagnosis_reports WHERE session_id = ? AND profile_id = ? ORDER BY created_at DESC LIMIT ?", (session_id, profile_id, max(1, min(limit, 100)))).fetchall()]
    finally: conn.close()


def list_recent_reports(profile_id: str, limit: int = 5) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, session_id, question, overall_score, created_at, diagnosis_json FROM diagnosis_reports WHERE profile_id = ? ORDER BY created_at DESC LIMIT ?", (profile_id, max(1, min(limit, 20)))).fetchall()
        return [{"id": row["id"], "session_id": row["session_id"], "question": row["question"], "overall_score": row["overall_score"], "missing_exam_points": [item.get("point", "") for item in json.loads(row["diagnosis_json"] or "{}").get("exam_points", []) if item.get("status") != "covered"][:5], "created_at": row["created_at"]} for row in rows]
    finally: conn.close()
