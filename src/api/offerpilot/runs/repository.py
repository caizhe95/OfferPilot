"""Transactional stores for Sessions, Runs, events, and approvals."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from offerpilot.database.connection import get_db
from offerpilot.database.values import dumps, loads, now
from offerpilot.profiles.repository import ensure_profile

RUN_TYPES = {"coach", "diagnosis", "audio_transcription", "report_export"}
RUN_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
RUN_ACTIVE = {"pending", "running", "waiting_approval"}
RUN_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "waiting_approval", "cancelled", "interrupted"},
    "running": {"waiting_approval", "completed", "failed", "cancelled", "interrupted"},
    "waiting_approval": {"running", "completed", "failed", "cancelled", "interrupted"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
    "interrupted": set(),
}

def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return max(0, int(round((end - start).total_seconds() * 1000)))


def _run_timing_conn(conn: Any, run_id: str, run_row: Any | None = None) -> dict[str, int]:
    row = run_row or conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise LookupError("run_not_found")
    created = _timestamp(row["created_at"])
    completed = _timestamp(row["completed_at"])
    if created is None or completed is None:
        return {
            "queue_duration_ms": 0,
            "approval_wait_ms": 0,
            "active_duration_ms": 0,
            "total_duration_ms": 0,
        }

    total_ms = _elapsed_ms(created, completed)
    started = _timestamp(row["started_at"]) or completed
    started = min(max(started, created), completed)
    queue_ms = min(total_ms, _elapsed_ms(created, started))

    approval_wait_ms = 0
    approvals = conn.execute(
        "SELECT created_at, resolved_at FROM approvals WHERE run_id = ? AND profile_id = ? ORDER BY created_at ASC",
        (run_id, row["profile_id"]),
    ).fetchall()
    for approval in approvals:
        approval_created = _timestamp(approval["created_at"])
        if approval_created is None:
            continue
        wait_start = min(max(approval_created, started), completed)
        wait_end = _timestamp(approval["resolved_at"]) or completed
        wait_end = min(max(wait_end, wait_start), completed)
        approval_wait_ms += _elapsed_ms(wait_start, wait_end)

    approval_wait_ms = min(approval_wait_ms, max(0, total_ms - queue_ms))
    active_ms = total_ms - queue_ms - approval_wait_ms
    return {
        "queue_duration_ms": queue_ms,
        "approval_wait_ms": approval_wait_ms,
        "active_duration_ms": active_ms,
        "total_duration_ms": total_ms,
    }


def _run_row(row: Any, conn: Any) -> dict[str, Any]:
    status = row["status"]
    timing = _run_timing_conn(conn, row["id"], row) if status in RUN_TERMINAL else None
    pending = conn.execute(
        "SELECT * FROM approvals WHERE run_id = ? AND profile_id = ? AND status = 'pending' ORDER BY created_at ASC LIMIT 1",
        (row["id"], row["profile_id"]),
    ).fetchone()
    public_approval = None
    if pending is not None:
        public_approval = {
            "id": pending["id"],
            "run_id": pending["run_id"],
            "trace_id": pending["trace_id"] or row["trace_id"] or row["id"],
            "tool_name": pending["tool_name"],
            "risk_level": pending["risk_level"],
            "flow_kind": pending["flow_kind"],
            "public_params": loads(pending["public_params"], {}),
            "decision": pending["decision"],
        }
    last_sequence = int(conn.execute("SELECT COALESCE(MAX(sequence), 0) FROM run_events WHERE run_id = ?", (row["id"],)).fetchone()[0])
    return {
        "id": row["id"],
        "run_id": row["id"],
        "trace_id": row["trace_id"] or row["id"],
        "session_id": row["session_id"],
        "profile_id": row["profile_id"],
        "type": row["run_type"],
        "run_type": row["run_type"],
        "status": row["status"],
        "idempotency_key": row["idempotency_key"],
        "input": loads(row["input_json"], {}),
        "state": loads(row["state_json"], {}),
        "result": loads(row["result_json"], {}),
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "heartbeat_at": row["heartbeat_at"],
        "completed_at": row["completed_at"],
        "cancel_requested_at": row["cancel_requested_at"],
        "timing": timing,
        "pending_approval": public_approval,
        "last_event_sequence": last_sequence,
    }


def create_run(profile_id: str, session_id: str, run_type: str, input_data: dict[str, Any], idempotency_key: str) -> tuple[dict[str, Any], bool]:
    if run_type not in RUN_TYPES:
        raise ValueError("invalid_run_type")
    if not idempotency_key.strip():
        raise ValueError("idempotency_key_required")
    ensure_profile(profile_id)
    run_id = str(uuid.uuid4())
    timestamp = now()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        session = conn.execute("SELECT id FROM sessions WHERE id = ? AND profile_id = ? AND status = 'active'", (session_id, profile_id)).fetchone()
        if session is None:
            conn.rollback()
            raise LookupError("session_not_found")
        existing = conn.execute(
            "SELECT * FROM runs WHERE profile_id = ? AND session_id = ? AND idempotency_key = ?",
            (profile_id, session_id, idempotency_key),
        ).fetchone()
        if existing:
            conn.commit()
            return _run_row(existing, conn), True
        active = conn.execute(
            "SELECT id FROM runs WHERE session_id = ? AND status IN ('pending', 'running', 'waiting_approval') LIMIT 1",
            (session_id,),
        ).fetchone()
        if active:
            conn.rollback()
            raise RuntimeError("session_run_conflict")
        conn.execute(
            "INSERT INTO runs(id, trace_id, session_id, profile_id, run_type, status, idempotency_key, input_json, created_at, updated_at, heartbeat_at) VALUES(?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
            (run_id, run_id, session_id, profile_id, run_type, idempotency_key, dumps(input_data), timestamp, timestamp, timestamp),
        )
        conn.execute(
            "INSERT INTO run_events(run_id, session_id, profile_id, sequence, event_type, data, created_at) VALUES(?, ?, ?, 1, 'run_created', ?, ?)",
            (run_id, session_id, profile_id, dumps({"type": run_type}), timestamp),
        )
        conn.commit()
    finally:
        conn.close()
    return get_run(run_id, profile_id) or {}, False


def get_run(run_id: str, profile_id: str | None = None) -> dict[str, Any] | None:
    conn = get_db()
    try:
        sql = "SELECT * FROM runs WHERE id = ?"
        params: list[Any] = [run_id]
        if profile_id is not None:
            sql += " AND profile_id = ?"
            params.append(profile_id)
        row = conn.execute(sql, params).fetchone()
        return _run_row(row, conn) if row else None
    finally:
        conn.close()


def list_runs(session_id: str, profile_id: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM runs WHERE session_id = ? AND profile_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, profile_id, max(1, min(limit, 100))),
        ).fetchall()
        return [_run_row(row, conn) for row in rows]
    finally:
        conn.close()


def list_active_run_ids(*, session_id: str | None = None, profile_id: str) -> list[str]:
    conn = get_db()
    try:
        params: list[Any] = [profile_id]
        sql = "SELECT id FROM runs WHERE profile_id = ? AND status IN ('pending', 'running', 'waiting_approval')"
        if session_id is not None:
            sql += " AND session_id = ?"
            params.append(session_id)
        rows = conn.execute(sql + " ORDER BY created_at ASC", params).fetchall()
        return [str(row["id"]) for row in rows]
    finally:
        conn.close()


def _append_event_conn(conn: Any, run_id: str, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute("SELECT session_id, profile_id, trace_id FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise LookupError("run_not_found")
    sequence = int(conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?", (run_id,)).fetchone()[0])
    timestamp = now()
    conn.execute(
        "INSERT INTO run_events(run_id, session_id, profile_id, sequence, event_type, data, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (run_id, row["session_id"], row["profile_id"], sequence, event_type, dumps(data), timestamp),
    )
    return {
        "type": event_type,
        "session_id": row["session_id"],
        "profile_id": row["profile_id"],
        "trace_id": row["trace_id"] or run_id,
        "run_id": run_id,
        "sequence": sequence,
        "created_at": timestamp,
        "data": data,
    }


def _append_terminal_events_conn(
    conn: Any,
    run_id: str,
    event_type: str,
    status: str,
    data: dict[str, Any],
) -> None:
    """Write normalized terminal state plus the compatibility event."""
    terminal_status = status if status in {"completed", "waiting_approval", "failed", "cancelled"} else "failed"
    timing = data.get("timing") or _run_timing_conn(conn, run_id)
    _append_event_conn(conn, run_id, "run_complete", {"status": terminal_status, "timing": timing})
    _append_event_conn(conn, run_id, event_type, {**data, "run_complete": {"status": terminal_status}})


def append_event(run_id: str, event_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        event = _append_event_conn(conn, run_id, event_type, data or {})
        conn.commit()
        return event
    finally:
        conn.close()


def transition_run(run_id: str, new_status: str, *, state: dict[str, Any] | None = None, result: dict[str, Any] | None = None, error_code: str = "", error_message: str = "", event_type: str | None = None, event_data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    updated: dict[str, Any] | None = None
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            conn.rollback()
            return None
        current = row["status"]
        if current in RUN_TERMINAL:
            if new_status == current:
                conn.commit()
                return _run_row(row, conn)
            conn.rollback()
            raise ValueError(f"invalid_run_transition:{current}->{new_status}")
        if new_status != current and new_status not in RUN_TRANSITIONS.get(current, set()):
            conn.rollback()
            raise ValueError(f"invalid_run_transition:{current}->{new_status}")
        timestamp = now()
        started = row["started_at"] or (timestamp if new_status == "running" else "")
        completed = timestamp if new_status in RUN_TERMINAL else row["completed_at"]
        heartbeat = timestamp
        conn.execute(
            "UPDATE runs SET status = ?, state_json = ?, result_json = ?, error_code = ?, error_message = ?, started_at = ?, updated_at = ?, heartbeat_at = ?, completed_at = ? WHERE id = ? AND profile_id = ?",
            (new_status, dumps(state if state is not None else loads(row["state_json"], {})), dumps(result if result is not None else loads(row["result_json"], {})), error_code[:120], error_message[:500], started, timestamp, heartbeat, completed, run_id, row["profile_id"]),
        )
        updated_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if event_type:
            payload = dict(event_data or {})
            if new_status in RUN_TERMINAL:
                payload["timing"] = _run_timing_conn(conn, run_id, updated_row)
                _append_terminal_events_conn(conn, run_id, event_type, new_status, payload)
            else:
                _append_event_conn(conn, run_id, event_type, payload)
        conn.commit()
        updated = _run_row(updated_row, conn)
    finally:
        conn.close()
    return updated


def get_run_timing(run_id: str) -> dict[str, int]:
    conn = get_db()
    try:
        return _run_timing_conn(conn, run_id)
    finally:
        conn.close()


def begin_run(run_id: str) -> dict[str, Any] | None:
    """Atomically move one uncancelled pending Run into execution."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            conn.rollback()
            return None
        if row["status"] != "pending" or row["cancel_requested_at"]:
            conn.commit()
            return _run_row(row, conn)
        timestamp = now()
        conn.execute(
            "UPDATE runs SET status = 'running', started_at = ?, updated_at = ?, heartbeat_at = ? WHERE id = ?",
            (timestamp, timestamp, timestamp, run_id),
        )
        _append_event_conn(conn, run_id, "run_started", {"run_type": row["run_type"]})
        conn.commit()
    finally:
        conn.close()
    return get_run(run_id)


def claim_pending_run(run_id: str) -> dict[str, Any] | None:
    """Compatibility alias for callers that still use the old repository name."""
    return begin_run(run_id)


def save_run_state(run_id: str, state: dict[str, Any]) -> dict[str, Any] | None:
    conn = get_db()
    try:
        timestamp = now()
        cursor = conn.execute(
            "UPDATE runs SET state_json = ?, updated_at = ?, heartbeat_at = ? "
            "WHERE id = ? AND status = 'running' AND cancel_requested_at = ''",
            (dumps(state), timestamp, timestamp, run_id),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return None
    finally:
        conn.close()
    return get_run(run_id)


def request_cancel(run_id: str, profile_id: str) -> bool:
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE runs SET cancel_requested_at = ?, updated_at = ? WHERE id = ? AND profile_id = ? AND status IN ('pending', 'running', 'waiting_approval')",
            (now(), now(), run_id, profile_id),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def events_after(
    run_id: str,
    after: int = 0,
    limit: int = 500,
    profile_id: str | None = None,
) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        params: list[Any] = [run_id]
        ownership = ""
        if profile_id is not None:
            ownership = " AND e.profile_id = ?"
            params.append(profile_id)
        params.extend([max(0, after), max(1, min(limit, 1000))])
        rows = conn.execute(
            "SELECT e.*, r.trace_id FROM run_events e JOIN runs r ON r.id = e.run_id AND r.profile_id = e.profile_id "
            "WHERE e.run_id = ? AND e.profile_id = r.profile_id" + ownership + " AND e.sequence > ? ORDER BY e.sequence ASC LIMIT ?",
            params,
        ).fetchall()
        return [
            {
                "type": row["event_type"],
                "session_id": row["session_id"],
                "trace_id": row["trace_id"] or row["run_id"],
                "run_id": row["run_id"],
                "sequence": row["sequence"],
                "created_at": row["created_at"],
                "data": loads(row["data"], {}),
            }
            for row in rows
        ]
    finally:
        conn.close()


def list_admin_runs(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        params: list[Any] = []
        sql = "SELECT * FROM runs"
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [_run_row(row, conn) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
