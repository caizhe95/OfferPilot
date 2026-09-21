"""Restart recovery and periodic approval cleanup for durable Runs."""

from __future__ import annotations

from typing import Any

from offerpilot.database.connection import get_db
from offerpilot.database.values import loads
from offerpilot.runs.operation_logs import write_log_in_transaction


def recover_runs() -> list[str]:
    """Mark abandoned active work as interrupted after a process restart."""
    from offerpilot.runs.repository import RUN_ACTIVE, _append_terminal_events_conn, _run_timing_conn

    conn = get_db()
    recovered: list[str] = []
    audio_cleanup: list[tuple[str, str, str, str]] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT * FROM runs WHERE status = 'running'").fetchall()
        for row in rows:
            run_id, timestamp = str(row["id"]), _now()
            if row["run_type"] == "audio_transcription":
                input_data = loads(row["input_json"], {})
                upload_id = input_data.get("upload_id") if isinstance(input_data, dict) else None
                if isinstance(upload_id, str) and upload_id:
                    audio_cleanup.append((upload_id, str(row["session_id"]), str(row["profile_id"]), run_id))
            conn.execute(
                "UPDATE runs SET status = 'interrupted', error_code = 'runtime_interrupted', "
                "updated_at = ?, completed_at = ? WHERE id = ?",
                (timestamp, timestamp, run_id),
            )
            updated = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            _append_terminal_events_conn(
                conn,
                run_id,
                "run_interrupted",
                "interrupted",
                {"reason": "runtime_restart", "timing": _run_timing_conn(conn, run_id, updated)},
            )
            write_log_in_transaction(
                conn,
                "run_interrupted",
                f"{row['run_type']} Run interrupted",
                profile_id=row["profile_id"],
                session_id=row["session_id"],
                run_id=run_id,
                metadata={"status": "interrupted", "error_code": "runtime_interrupted"},
                level="error",
            )
            recovered.append(run_id)
        rows = conn.execute(
            "SELECT * FROM runs WHERE status IN ('pending', 'waiting_approval') "
            "AND cancel_requested_at <> ''"
        ).fetchall()
        for row in rows:
            run_id, timestamp = str(row["id"]), _now()
            conn.execute(
                "UPDATE runs SET status = 'cancelled', error_code = 'cancelled', "
                "updated_at = ?, completed_at = ? WHERE id = ?",
                (timestamp, timestamp, run_id),
            )
            updated = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            _append_terminal_events_conn(
                conn,
                run_id,
                "run_cancelled",
                "cancelled",
                {"reason": "cancel_requested", "timing": _run_timing_conn(conn, run_id, updated)},
            )
            recovered.append(run_id)
        approvals = conn.execute("SELECT id, run_id FROM approvals WHERE status = 'executing'").fetchall()
        for approval in approvals:
            approval_id, run_id, timestamp = str(approval["id"]), str(approval["run_id"]), _now()
            conn.execute(
                "UPDATE approvals SET status = 'failed', executed_at = ?, error = 'approval_interrupted' "
                "WHERE id = ?",
                (timestamp, approval_id),
            )
            run = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run and run["status"] in RUN_ACTIVE:
                conn.execute(
                    "UPDATE runs SET status = 'interrupted', error_code = 'approval_interrupted', "
                    "updated_at = ?, completed_at = ? WHERE id = ?",
                    (timestamp, timestamp, run_id),
                )
                updated = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
                _append_terminal_events_conn(
                    conn,
                    run_id,
                    "run_interrupted",
                    "interrupted",
                    {"reason": "approval_interrupted", "timing": _run_timing_conn(conn, run_id, updated)},
                )
                write_log_in_transaction(
                    conn,
                    "run_interrupted",
                    f"{updated['run_type']} Run interrupted",
                    profile_id=updated["profile_id"],
                    session_id=updated["session_id"],
                    run_id=run_id,
                    metadata={"status": "interrupted", "error_code": "approval_interrupted"},
                    level="error",
                )
                recovered.append(run_id)
        if recovered:
            write_log_in_transaction(
                conn,
                "runtime_recovered",
                "Runtime recovery completed",
                metadata={"count": len(set(recovered))},
            )
        conn.commit()
        result = list(dict.fromkeys(recovered))
    finally:
        conn.close()
    if audio_cleanup:
        from offerpilot.audio.storage import finalize_audio_upload

        for upload_id, session_id, profile_id, run_id in audio_cleanup:
            finalize_audio_upload(
                upload_id,
                session_id,
                profile_id,
                "failed",
                error="runtime_interrupted",
                run_id=run_id,
            )
    return result


def list_pending_runs(limit: int = 100) -> list[str]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id FROM runs WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [str(row["id"]) for row in rows]
    finally:
        conn.close()


def list_resumable_runs(limit: int = 100) -> list[str]:
    """Return approval-resume work that is safe to dispatch after restart."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT DISTINCT r.id FROM runs r JOIN approvals a ON a.run_id = r.id "
            "WHERE r.status = 'waiting_approval' AND a.status IN ('approved', 'denied') "
            "ORDER BY r.updated_at ASC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [str(row["id"]) for row in rows]
    finally:
        conn.close()


def expire_approvals() -> list[dict[str, Any]]:
    """Expire undecided approvals and cancel their active Runs atomically."""
    from offerpilot.runs.repository import RUN_ACTIVE, _append_terminal_events_conn, _run_timing_conn

    timestamp = _now()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM approvals WHERE status IN ('pending', 'approved') "
            "AND expires_at <> '' AND expires_at <= ?",
            (timestamp,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE approvals SET status = 'expired', resolved_at = ?, error = 'approval_expired' WHERE id = ?",
                (timestamp, row["id"]),
            )
            run = conn.execute("SELECT status FROM runs WHERE id = ?", (row["run_id"],)).fetchone()
            if run and run["status"] in RUN_ACTIVE:
                conn.execute(
                    "UPDATE runs SET status = 'cancelled', error_code = 'approval_expired', "
                    "updated_at = ?, completed_at = ? WHERE id = ?",
                    (timestamp, timestamp, row["run_id"]),
                )
                updated = conn.execute("SELECT * FROM runs WHERE id = ?", (row["run_id"],)).fetchone()
                _append_terminal_events_conn(
                    conn,
                    str(row["run_id"]),
                    "run_cancelled",
                    "cancelled",
                    {"reason": "approval_expired", "timing": _run_timing_conn(conn, str(row["run_id"]), updated)},
                )
        conn.commit()
        return [
            {
                "run_id": str(row["run_id"]),
                "session_id": str(row["session_id"]),
                "profile_id": str(row["profile_id"]),
                "flow_kind": str(row["flow_kind"]),
                "params": loads(row["params"], {}),
            }
            for row in rows
        ]
    finally:
        conn.close()
def _now() -> str:
    # Resolve through repository so existing operational tests and callers can
    # freeze the clock at the same boundary as Run transitions.
    from offerpilot.runs import repository

    return repository.now()
