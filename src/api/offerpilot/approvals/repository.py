"""SQLite persistence and idempotent transitions for approvals."""

from __future__ import annotations

import uuid
from typing import Any

from offerpilot.database.connection import get_db
from offerpilot.database.values import dumps, expires, loads, now


APPROVAL_FLOW_KINDS = {"coach", "audio", "export"}


def create_approval(
    run_id: str,
    session_id: str,
    profile_id: str,
    tool_name: str,
    risk_level: str,
    params: dict[str, Any],
    flow_kind: str,
    public_params: dict[str, Any] | None = None,
) -> str:
    if flow_kind not in APPROVAL_FLOW_KINDS:
        raise ValueError("invalid_approval_flow")
    approval_id = str(uuid.uuid4())
    timestamp = now()
    conn = get_db()
    try:
        run = conn.execute(
            "SELECT trace_id FROM runs WHERE id = ? AND session_id = ? AND profile_id = ?",
            (run_id, session_id, profile_id),
        ).fetchone()
        if run is None:
            raise LookupError("approval_run_not_found")
        conn.execute(
            "INSERT INTO approvals(id, run_id, trace_id, session_id, profile_id, tool_name, risk_level, flow_kind, "
            "params, public_params, status, created_at, expires_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (
                approval_id,
                run_id,
                run["trace_id"] or run_id,
                session_id,
                profile_id,
                tool_name,
                risk_level,
                flow_kind,
                dumps(params),
                dumps(public_params or {}),
                timestamp,
                expires(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return approval_id


def _approval_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "request_id": row["id"],
        "run_id": row["run_id"],
        "trace_id": row["trace_id"] or row["run_id"],
        "session_id": row["session_id"],
        "profile_id": row["profile_id"],
        "tool_name": row["tool_name"],
        "risk_level": row["risk_level"],
        "flow_kind": row["flow_kind"],
        "params": loads(row["params"], {}),
        "public_params": loads(row["public_params"], {}),
        "status": row["status"],
        "decision": row["decision"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
        "expires_at": row["expires_at"],
        "executed_at": row["executed_at"],
        "error": row["error"],
    }


def get_approval(approval_id: str, profile_id: str | None = None) -> dict[str, Any] | None:
    conn = get_db()
    try:
        sql = "SELECT * FROM approvals WHERE id = ?"
        params: list[Any] = [approval_id]
        if profile_id is not None:
            sql += " AND profile_id = ?"
            params.append(profile_id)
        row = conn.execute(sql, params).fetchone()
        return _approval_row(row) if row else None
    finally:
        conn.close()


def list_approvals(session_id: str, profile_id: str) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE session_id = ? AND profile_id = ? ORDER BY created_at DESC",
            (session_id, profile_id),
        ).fetchall()
        return [_approval_row(row) for row in rows]
    finally:
        conn.close()


def decide_approval(approval_id: str, profile_id: str, decision: str) -> dict[str, Any] | None:
    if decision not in {"approve", "deny"}:
        raise ValueError("invalid_approval_decision")
    expired_audio: dict[str, Any] | None = None
    expired_run_id = ""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM approvals WHERE id = ? AND profile_id = ?", (approval_id, profile_id)
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        previous_decision = str(row["decision"])
        if previous_decision:
            if previous_decision == decision:
                conn.commit()
                return {**_approval_row(row), "decision_reused": True}
            conn.rollback()
            raise RuntimeError("approval_decision_conflict")
        if row["status"] != "pending":
            conn.rollback()
            raise RuntimeError("approval_not_decidable")
        timestamp = now()
        if row["expires_at"] and str(row["expires_at"]) <= timestamp:
            expired_audio, expired_run_id = _expire_approval_conn(conn, row, timestamp)
            conn.commit()
            _notify_expired_run(expired_run_id)
            _cleanup_expired_audio(expired_audio)
            raise RuntimeError("approval_expired")
        conn.execute(
            "UPDATE approvals SET status = ?, decision = ?, resolved_at = ? WHERE id = ?",
            ("approved" if decision == "approve" else "denied", decision, timestamp, approval_id),
        )
        conn.commit()
    finally:
        conn.close()
    resolved = get_approval(approval_id, profile_id)
    return {**resolved, "decision_reused": False} if resolved else None


def claim_approval(approval_id: str, profile_id: str) -> dict[str, Any] | None:
    expired_audio: dict[str, Any] | None = None
    expired_run_id = ""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        timestamp = now()
        row = conn.execute(
            "SELECT * FROM approvals WHERE id = ? AND profile_id = ?", (approval_id, profile_id)
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        if row["status"] == "approved" and row["expires_at"] and str(row["expires_at"]) <= timestamp:
            expired_audio, expired_run_id = _expire_approval_conn(conn, row, timestamp)
            conn.commit()
            _notify_expired_run(expired_run_id)
            _cleanup_expired_audio(expired_audio)
            return None
        cursor = conn.execute(
            "UPDATE approvals SET status = 'executing' WHERE id = ? AND profile_id = ? "
            "AND status = 'approved' AND decision = 'approve' "
            "AND (expires_at = '' OR expires_at > ?)",
            (approval_id, profile_id, timestamp),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return None
    finally:
        conn.close()
    return get_approval(approval_id, profile_id)


def _expire_approval_conn(conn: Any, row: Any, timestamp: str) -> tuple[dict[str, Any] | None, str]:
    """Expire one approval and close its active Run in the same transaction."""
    conn.execute(
        "UPDATE approvals SET status = 'expired', resolved_at = ?, error = 'approval_expired' WHERE id = ?",
        (timestamp, row["id"]),
    )
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (row["run_id"],)).fetchone()
    run_id = str(row["run_id"])
    if run is None or run["status"] not in {"pending", "running", "waiting_approval"}:
        return None, ""
    conn.execute(
        "UPDATE runs SET status = 'cancelled', error_code = 'approval_expired', "
        "updated_at = ?, heartbeat_at = ?, completed_at = ? WHERE id = ? "
        "AND status IN ('pending', 'running', 'waiting_approval')",
        (timestamp, timestamp, timestamp, run_id),
    )
    updated = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if updated is not None and updated["status"] == "cancelled":
        from offerpilot.runs.repository import _append_terminal_events_conn, _run_timing_conn

        _append_terminal_events_conn(
            conn,
            run_id,
            "run_cancelled",
            "cancelled",
            {"reason": "approval_expired", "timing": _run_timing_conn(conn, run_id, updated)},
        )
    params = loads(row["params"], {})
    upload_id = params.get("upload_id") if isinstance(params, dict) else None
    if row["flow_kind"] == "audio" and isinstance(upload_id, str):
        return {
            "upload_id": upload_id,
            "session_id": str(row["session_id"]),
            "profile_id": str(row["profile_id"]),
            "run_id": run_id,
        }, run_id
    return None, run_id


def _cleanup_expired_audio(audio: dict[str, Any] | None) -> None:
    if not audio:
        return
    from offerpilot.audio.storage import finalize_audio_upload

    finalize_audio_upload(
        str(audio["upload_id"]),
        str(audio["session_id"]),
        str(audio["profile_id"]),
        "expired",
        error="approval expired",
        run_id=str(audio["run_id"]),
    )


def _notify_expired_run(run_id: str) -> None:
    if not run_id:
        return
    from offerpilot.runs.events import notify

    notify(run_id)


def finish_approval(approval_id: str, success: bool, error: str = "") -> None:
    conn = get_db()
    try:
        conn.execute(
            "UPDATE approvals SET status = ?, executed_at = ?, error = ? "
            "WHERE id = ? AND status = 'executing'",
            ("executed" if success else "failed", now(), error[:500], approval_id),
        )
        conn.commit()
    finally:
        conn.close()


def cancel_open_approvals(run_id: str, *, error: str = "run_cancelled") -> list[str]:
    """Close unresolved approvals once their owning Run reaches a terminal state."""
    timestamp = now()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id FROM approvals WHERE run_id = ? AND status IN ('pending', 'approved', 'executing')",
            (run_id,),
        ).fetchall()
        conn.execute(
            "UPDATE approvals SET status = 'failed', "
            "resolved_at = CASE WHEN resolved_at = '' THEN ? ELSE resolved_at END, error = ? "
            "WHERE run_id = ? AND status IN ('pending', 'approved', 'executing')",
            (timestamp, error[:500], run_id),
        )
        conn.commit()
        return [str(row["id"]) for row in rows]
    finally:
        conn.close()
