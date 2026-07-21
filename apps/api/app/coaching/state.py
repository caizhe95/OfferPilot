"""Durable Coach-run and approval state used across process restarts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.database import get_db


def save_run(session_id: str, profile_id: str, trace_id: str, status: str, state: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO coach_runs (session_id, profile_id, trace_id, status, state_json, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET trace_id=excluded.trace_id, status=excluded.status, state_json=excluded.state_json, updated_at=excluded.updated_at",
            (session_id, profile_id, trace_id, status, json.dumps(state, ensure_ascii=False), now),
        )
        conn.commit()
    finally:
        conn.close()


def get_run(session_id: str, profile_id: str) -> dict[str, Any] | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM coach_runs WHERE session_id = ? AND profile_id = ?", (session_id, profile_id)).fetchone()
        if row is None:
            return None
        return {"session_id": row["session_id"], "trace_id": row["trace_id"], "status": row["status"], "state": json.loads(row["state_json"]), "updated_at": row["updated_at"]}
    finally:
        conn.close()


def create_approval(session_id: str, profile_id: str, tool_name: str, risk_level: str, params: dict[str, Any]) -> str:
    request_id = str(uuid.uuid4())
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO approval_requests (id, session_id, profile_id, tool_name, risk_level, params) VALUES (?, ?, ?, ?, ?, ?)",
            (request_id, session_id, profile_id, tool_name, risk_level, json.dumps(params, ensure_ascii=False)),
        )
        conn.commit()
        return request_id
    finally:
        conn.close()


def get_approval(request_id: str, session_id: str, profile_id: str) -> dict[str, Any] | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM approval_requests WHERE id = ? AND session_id = ? AND profile_id = ?", (request_id, session_id, profile_id)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "session_id": row["session_id"], "profile_id": row["profile_id"], "tool_name": row["tool_name"], "risk_level": row["risk_level"], "params": json.loads(row["params"]), "status": row["status"]}
    finally:
        conn.close()


def resolve_approval(request_id: str, session_id: str, profile_id: str, status: str) -> dict[str, Any] | None:
    approval = get_approval(request_id, session_id, profile_id)
    if approval is None or approval["status"] != "pending":
        return None
    conn = get_db()
    try:
        conn.execute("UPDATE approval_requests SET status = ?, resolved_at = ? WHERE id = ?", (status, datetime.now(timezone.utc).isoformat(), request_id))
        conn.commit()
    finally:
        conn.close()
    approval["status"] = status
    return approval
