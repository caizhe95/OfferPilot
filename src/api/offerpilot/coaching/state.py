"""Durable, transactional Run and approval state shared by API workers."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from offerpilot.core.database import get_db

APPROVAL_STATES = {"pending", "approved", "denied", "executing", "executed", "failed", "expired"}
APPROVAL_FLOW_KINDS = {"coach", "audio", "export"}
ACTIVE_RUN_STATES = {"running", "cancel_requested"}
RUN_TERMINAL_STATES = {"completed", "failed", "cancelled"}


class _StrictParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _CoachApprovalParams(_StrictParams):
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=300)
    category: str = Field(default="general", max_length=64)


class _AudioApprovalParams(_StrictParams):
    upload_id: str = Field(min_length=1, max_length=64)


class _ExportApprovalParams(_StrictParams):
    session_id: str = Field(min_length=1, max_length=64)
    report_id: str = Field(min_length=1, max_length=64)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires(hours: int = 24) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _decode_json(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _normalize_approval_params(flow_kind: str, tool_name: str, params: dict[str, Any], session_id: str) -> dict[str, Any]:
    if flow_kind == "coach" and tool_name == "save_memory":
        return _CoachApprovalParams.model_validate(params).model_dump()
    if flow_kind == "audio" and tool_name == "transcribe_audio":
        return _AudioApprovalParams.model_validate(params).model_dump()
    if flow_kind == "export" and tool_name == "export_report":
        normalized = _ExportApprovalParams.model_validate(params).model_dump()
        if normalized["session_id"] != session_id:
            raise ValueError("Export approval session does not match")
        return normalized
    raise ValueError("Unsupported approval tool for flow")


def begin_run(session_id: str, profile_id: str, run_kind: str) -> dict[str, Any] | None:
    """Atomically claim a ready session, create its trace, and persist its Run."""
    if run_kind not in {"coach", "diagnosis"}:
        raise ValueError("Unsupported run kind")
    trace_id = str(uuid.uuid4())
    now = _now()
    state = {"run_kind": run_kind}
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        updated = conn.execute(
            """
            UPDATE sessions SET status = 'running', updated_at = ?
            WHERE id = ? AND profile_id = ? AND status = 'ready'
            """,
            (now, session_id, profile_id),
        )
        if updated.rowcount != 1:
            conn.rollback()
            return None
        conn.execute(
            "INSERT INTO traces (id, session_id, status, created_at, updated_at) VALUES (?, ?, 'running', ?, ?)",
            (trace_id, session_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO coach_runs
                (session_id, profile_id, trace_id, status, state_json, run_kind, started_at, updated_at, completed_at)
            VALUES (?, ?, ?, 'running', ?, ?, ?, ?, '')
            ON CONFLICT(session_id) DO UPDATE SET
                profile_id=excluded.profile_id,
                trace_id=excluded.trace_id,
                status='running',
                state_json=excluded.state_json,
                run_kind=excluded.run_kind,
                started_at=excluded.started_at,
                updated_at=excluded.updated_at,
                completed_at=''
            """,
            (session_id, profile_id, trace_id, json.dumps(state, ensure_ascii=False), run_kind, now, now),
        )
        conn.commit()
        return {
            "session_id": session_id,
            "profile_id": profile_id,
            "trace_id": trace_id,
            "status": "running",
            "state": state,
            "run_kind": run_kind,
            "started_at": now,
            "updated_at": now,
            "completed_at": "",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def begin_coach_resume(session_id: str, profile_id: str, request_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Atomically move exactly one matching paused Coach Run back to running."""
    now = _now()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        run_row = conn.execute(
            "SELECT * FROM coach_runs WHERE session_id = ? AND profile_id = ? AND status = 'waiting_approval'",
            (session_id, profile_id),
        ).fetchone()
        approval_row = conn.execute(
            "SELECT * FROM approval_requests WHERE id = ? AND session_id = ? AND profile_id = ?",
            (request_id, session_id, profile_id),
        ).fetchone()
        if run_row is None or approval_row is None:
            conn.rollback()
            return None
        run = _run_row(run_row)
        approval = _approval_row(approval_row)
        if (
            approval["flow_kind"] != "coach"
            or approval["status"] not in {"approved", "denied"}
            or _approval_expired(approval, now)
            or approval["trace_id"] != run["trace_id"]
            or run["state"].get("request_id") != request_id
        ):
            conn.rollback()
            return None
        run_updated = conn.execute(
            """
            UPDATE coach_runs SET status = 'running', updated_at = ?, completed_at = ''
            WHERE session_id = ? AND profile_id = ? AND trace_id = ? AND status = 'waiting_approval'
            """,
            (now, session_id, profile_id, run["trace_id"]),
        )
        session_updated = conn.execute(
            "UPDATE sessions SET status = 'running', updated_at = ? WHERE id = ? AND profile_id = ? AND status = 'waiting_approval'",
            (now, session_id, profile_id),
        )
        if run_updated.rowcount != 1 or session_updated.rowcount != 1:
            conn.rollback()
            return None
        conn.commit()
        run["status"] = "running"
        run["updated_at"] = now
        return run, approval
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_run(
    session_id: str,
    profile_id: str,
    trace_id: str,
    status: str,
    state: dict[str, Any],
    *,
    run_kind: str | None = None,
) -> bool:
    """Persist a state transition only when it still belongs to the active trace."""
    if status not in ACTIVE_RUN_STATES | RUN_TERMINAL_STATES | {"waiting_approval"}:
        raise ValueError("Invalid Run status")
    now = _now()
    terminal = status in RUN_TERMINAL_STATES
    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO coach_runs
                (session_id, profile_id, trace_id, status, state_json, run_kind, started_at, updated_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                status=excluded.status,
                state_json=excluded.state_json,
                run_kind=excluded.run_kind,
                updated_at=excluded.updated_at,
                completed_at=excluded.completed_at
            WHERE coach_runs.profile_id=excluded.profile_id AND coach_runs.trace_id=excluded.trace_id
            """,
            (
                session_id,
                profile_id,
                trace_id,
                status,
                json.dumps(state, ensure_ascii=False),
                run_kind or str(state.get("run_kind", "coach")),
                now,
                now,
                now if terminal else "",
            ),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def get_run(session_id: str, profile_id: str, trace_id: str | None = None) -> dict[str, Any] | None:
    conn = get_db()
    try:
        sql = "SELECT * FROM coach_runs WHERE session_id = ? AND profile_id = ?"
        params: list[str] = [session_id, profile_id]
        if trace_id:
            sql += " AND trace_id = ?"
            params.append(trace_id)
        row = conn.execute(sql, params).fetchone()
        return _run_row(row) if row else None
    finally:
        conn.close()


def request_cancel(session_id: str, profile_id: str, trace_id: str) -> bool:
    """Mark exactly the matching active turn for cancellation; repeated calls are safe."""
    conn = get_db()
    try:
        cursor = conn.execute(
            """
            UPDATE coach_runs SET status = 'cancel_requested', updated_at = ?
            WHERE session_id = ? AND profile_id = ? AND trace_id = ? AND status = 'running'
            """,
            (_now(), session_id, profile_id, trace_id),
        )
        if cursor.rowcount == 0:
            row = conn.execute(
                "SELECT status FROM coach_runs WHERE session_id = ? AND profile_id = ? AND trace_id = ?",
                (session_id, profile_id, trace_id),
            ).fetchone()
            conn.commit()
            return bool(row and row["status"] in {"cancel_requested", "cancelled"})
        conn.commit()
        return True
    finally:
        conn.close()


def recover_interrupted_runs(max_age_seconds: int = 90) -> list[dict[str, Any]]:
    """Fail stale active Runs and release their Sessions after an API restart."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat()
    conn = get_db()
    recovered: list[dict[str, Any]] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM coach_runs WHERE status IN ('running', 'cancel_requested') AND updated_at < ?",
            (cutoff,),
        ).fetchall()
        now = _now()
        for row in rows:
            conn.execute(
                "UPDATE coach_runs SET status = 'failed', updated_at = ?, completed_at = ? WHERE session_id = ? AND trace_id = ?",
                (now, now, row["session_id"], row["trace_id"]),
            )
            conn.execute("UPDATE traces SET status = 'failed', updated_at = ? WHERE id = ?", (now, row["trace_id"]))
            conn.execute(
                "UPDATE sessions SET status = 'ready', updated_at = ? WHERE id = ? AND status = 'running'",
                (now, row["session_id"]),
            )
            conn.execute(
                "INSERT INTO trace_events (trace_id, event_type, step_index, data, created_at) VALUES (?, ?, ?, ?, ?)",
                (row["trace_id"], "run_recovered", 0, json.dumps({"reason": "process_restart"}), now),
            )
            recovered.append(_run_row(row) | {"status": "failed", "completed_at": now})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return recovered


def create_approval(
    session_id: str,
    profile_id: str,
    tool_name: str,
    risk_level: str,
    params: dict[str, Any],
    *,
    flow_kind: str,
    trace_id: str | None = None,
    public_params: dict[str, Any] | None = None,
    expires_hours: int = 24,
) -> str:
    if flow_kind not in APPROVAL_FLOW_KINDS:
        raise ValueError("Unsupported approval flow")
    normalized_params = _normalize_approval_params(flow_kind, tool_name, params, session_id)
    request_id = str(uuid.uuid4())
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO approval_requests
                (id, session_id, profile_id, tool_name, risk_level, flow_kind, trace_id, params, public_params, status, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                request_id,
                session_id,
                profile_id,
                tool_name,
                risk_level,
                flow_kind,
                trace_id or "",
                json.dumps(normalized_params, ensure_ascii=False),
                json.dumps(public_params if public_params is not None else normalized_params, ensure_ascii=False),
                _now(),
                _expires(expires_hours),
            ),
        )
        conn.commit()
        return request_id
    finally:
        conn.close()


def get_approval(request_id: str, session_id: str, profile_id: str) -> dict[str, Any] | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM approval_requests WHERE id = ? AND session_id = ? AND profile_id = ?",
            (request_id, session_id, profile_id),
        ).fetchone()
        if row is None:
            return None
        approval = _approval_row(row)
        if _approval_expired(approval, _now()):
            conn.execute(
                "UPDATE approval_requests SET status = 'expired', resolved_at = ? WHERE id = ? AND status IN ('pending', 'approved')",
                (_now(), request_id),
            )
            conn.commit()
            approval["status"] = "expired"
        return approval
    finally:
        conn.close()


def list_active_approvals(session_id: str, profile_id: str) -> list[dict[str, Any]]:
    expired = expire_stale_approvals()
    if expired:
        release_stuck_sessions()
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM approval_requests
            WHERE session_id = ? AND profile_id = ? AND status IN ('pending', 'approved', 'executing')
            ORDER BY created_at DESC
            """,
            (session_id, profile_id),
        ).fetchall()
        return [_approval_row(row) for row in rows]
    finally:
        conn.close()


def resolve_approval(request_id: str, session_id: str, profile_id: str, status: str) -> dict[str, Any] | None:
    """Resolve an approval once; repeat requests return its same terminal decision."""
    if status not in {"approved", "denied"}:
        raise ValueError("approval resolution must be approved or denied")
    now = _now()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM approval_requests WHERE id = ? AND session_id = ? AND profile_id = ?",
            (request_id, session_id, profile_id),
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        approval = _approval_row(row)
        if _approval_expired(approval, now):
            conn.execute("UPDATE approval_requests SET status = 'expired', resolved_at = ? WHERE id = ?", (now, request_id))
            conn.commit()
            return None
        if approval["status"] == status:
            conn.commit()
            return approval
        if approval["status"] != "pending":
            conn.rollback()
            return None
        conn.execute("UPDATE approval_requests SET status = ?, resolved_at = ? WHERE id = ?", (status, now, request_id))
        conn.commit()
        approval["status"] = status
        approval["resolved_at"] = now
        return approval
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_approved_approval(request_id: str, session_id: str, profile_id: str) -> dict[str, Any] | None:
    """Atomically claim one approved execution, preventing duplicate side effects."""
    now = _now()
    conn = get_db()
    try:
        cursor = conn.execute(
            """
            UPDATE approval_requests SET status = 'executing'
            WHERE id = ? AND session_id = ? AND profile_id = ? AND status = 'approved'
              AND (expires_at = '' OR expires_at > ?)
            """,
            (request_id, session_id, profile_id, now),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return None
    finally:
        conn.close()
    return get_approval(request_id, session_id, profile_id)


def finish_approval(request_id: str, *, succeeded: bool, error: str = "") -> None:
    conn = get_db()
    try:
        now = _now()
        conn.execute(
            "UPDATE approval_requests SET status = ?, executed_at = ?, error = ? WHERE id = ? AND status = 'executing'",
            ("executed" if succeeded else "failed", now, error[:500], request_id),
        )
        conn.commit()
    finally:
        conn.close()


def expire_stale_approvals() -> list[dict[str, Any]]:
    """Expire every unresolved approval older than its 24-hour deadline."""
    now = _now()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT * FROM approval_requests
            WHERE status IN ('pending', 'approved') AND expires_at != '' AND expires_at <= ?
            """,
            (now,),
        ).fetchall()
        if rows:
            conn.execute(
                """
                UPDATE approval_requests SET status = 'expired', resolved_at = ?, error = 'approval expired'
                WHERE status IN ('pending', 'approved') AND expires_at != '' AND expires_at <= ?
                """,
                (now, now),
            )
        conn.commit()
        approvals = [_approval_row(row) for row in rows]
        for approval in approvals:
            approval["status"] = "expired"
            approval["resolved_at"] = now
        return approvals
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_stuck_sessions() -> int:
    """Release running/waiting Sessions that no longer have a live durable Run or approval."""
    now = _now()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE sessions
            SET status = 'ready', updated_at = ?
            WHERE status = 'running'
              AND NOT EXISTS (
                  SELECT 1 FROM coach_runs
                  WHERE coach_runs.session_id = sessions.id
                    AND coach_runs.status IN ('running', 'cancel_requested')
              )
            """,
            (now,),
        )
        released = cursor.rowcount
        cursor = conn.execute(
            """
            UPDATE sessions
            SET status = 'ready', updated_at = ?
            WHERE status = 'waiting_approval'
              AND NOT EXISTS (
                  SELECT 1 FROM approval_requests
                  WHERE approval_requests.session_id = sessions.id
                    AND approval_requests.status IN ('pending', 'approved', 'executing')
              )
            """,
            (now,),
        )
        released += cursor.rowcount
        conn.commit()
        return released
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sweep_runtime_state(max_age_seconds: int = 90) -> dict[str, Any]:
    """Perform one restart/periodic cleanup pass and return cleanup targets."""
    recovered = recover_interrupted_runs(max_age_seconds=max_age_seconds)
    expired = expire_stale_approvals()
    released = release_stuck_sessions()
    return {"recovered_runs": recovered, "expired_approvals": expired, "released_sessions": released}


def has_permission_grant(session_id: str, profile_id: str, tool_name: str) -> bool:
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT 1 FROM permission_grants
            WHERE session_id = ? AND profile_id = ? AND tool_name = ?
              AND (expires_at = '' OR expires_at > ?)
            LIMIT 1
            """,
            (session_id, profile_id, tool_name, _now()),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def grant_permission(session_id: str, profile_id: str, tool_name: str, *, expires_hours: int = 24) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO permission_grants (id, session_id, profile_id, tool_name, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), session_id, profile_id, tool_name, _now(), _expires(expires_hours)),
        )
        conn.commit()
    finally:
        conn.close()


def _approval_expired(approval: dict[str, Any], now: str) -> bool:
    return approval["status"] in {"pending", "approved"} and bool(approval["expires_at"]) and approval["expires_at"] <= now


def _run_row(row: Any) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "profile_id": row["profile_id"],
        "trace_id": row["trace_id"],
        "status": row["status"],
        "state": _decode_json(row["state_json"]),
        "run_kind": row["run_kind"] if "run_kind" in row.keys() else "coach",
        "started_at": row["started_at"] if "started_at" in row.keys() else row["updated_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"] if "completed_at" in row.keys() else "",
    }


def _approval_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "profile_id": row["profile_id"],
        "tool_name": row["tool_name"],
        "risk_level": row["risk_level"],
        "flow_kind": row["flow_kind"] if "flow_kind" in row.keys() else "legacy",
        "trace_id": row["trace_id"] if "trace_id" in row.keys() else "",
        "params": _decode_json(row["params"]),
        "public_params": _decode_json(row["public_params"]) if "public_params" in row.keys() else {},
        "status": row["status"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
        "expires_at": row["expires_at"] if "expires_at" in row.keys() else "",
        "executed_at": row["executed_at"] if "executed_at" in row.keys() else "",
        "error": row["error"] if "error" in row.keys() else "",
    }
