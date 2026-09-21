"""Redacted machine-facing operation log persistence."""

from __future__ import annotations

from typing import Any

from offerpilot.database.connection import get_db
from offerpilot.database.values import dumps, loads, now

_ALLOWED_METADATA = {
    "tool_name",
    "risk_level",
    "action",
    "result_code",
    "flow_kind",
    "success",
    "count",
    "error_code",
    "status",
}


def write_permission_log(
    session_id: str,
    tool_name: str,
    risk_level: str,
    action: str,
    *,
    result: str = "",
    profile_id: str | None = None,
    run_id: str | None = None,
) -> None:
    result_code = result if result in {"", "ok", "cancelled", "failed", "permission_denied", "tool_execution_failed"} else "completed"
    write_log(
        "permission_" + action,
        f"{tool_name} ({risk_level})",
        profile_id=profile_id,
        session_id=session_id,
        run_id=run_id,
        metadata={
            "tool_name": tool_name,
            "risk_level": risk_level,
            "action": action,
            "result_code": result_code,
        },
        audience="human" if action in {"approve", "deny"} else "machine",
    )


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, str | int | float | bool]:
    return {
        key: value
        for key, value in (metadata or {}).items()
        if key in _ALLOWED_METADATA and isinstance(value, (str, int, float, bool))
    }


def write_log(
    event_type: str,
    summary: str,
    *,
    profile_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    audience: str = "machine",
    level: str = "info",
) -> None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO operation_logs(profile_id, session_id, run_id, audience, level, event_type, summary, metadata, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                profile_id,
                session_id,
                run_id,
                audience,
                level,
                event_type,
                summary[:500],
                dumps(_safe_metadata(metadata)),
                now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def write_log_in_transaction(
    conn: Any,
    event_type: str,
    summary: str,
    *,
    profile_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    audience: str = "machine",
    level: str = "info",
) -> None:
    conn.execute(
        "INSERT INTO operation_logs(profile_id, session_id, run_id, audience, level, event_type, summary, metadata, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            profile_id,
            session_id,
            run_id,
            audience,
            level,
            event_type,
            summary[:500],
            dumps(_safe_metadata(metadata)),
            now(),
        ),
    )


def list_operation_logs(limit: int = 100) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, profile_id, session_id, run_id, audience, level, event_type, summary, metadata, created_at "
            "FROM operation_logs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [{**dict(row), "metadata": loads(row["metadata"], {})} for row in rows]
    finally:
        conn.close()
