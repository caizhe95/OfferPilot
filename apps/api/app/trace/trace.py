"""Trace system: CRUD for execution traces and events.

Kept minimal — no eval logic, no diagnosis-specific knowledge.
"""

import json
import uuid
from datetime import datetime, timezone
from app.core.database import get_db


def create_trace(session_id: str) -> dict:
    """Create a new trace for a session."""
    conn = get_db()
    try:
        trace_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO traces (id, session_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (trace_id, session_id, "running", now, now),
        )
        conn.commit()
        return {"id": trace_id, "session_id": session_id, "status": "running"}
    finally:
        conn.close()


def add_trace_event(
    trace_id: str,
    event_type: str,
    step_index: int | None = None,
    data: dict | None = None,
) -> dict:
    """Add an event to a trace."""
    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        data_json = json.dumps(data or {}, ensure_ascii=False)
        cursor = conn.execute(
            "INSERT INTO trace_events (trace_id, event_type, step_index, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (trace_id, event_type, step_index, data_json, now),
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "trace_id": trace_id,
            "event_type": event_type,
            "step_index": step_index,
            "data": data or {},
            "created_at": now,
        }
    finally:
        conn.close()


def complete_trace(trace_id: str, status: str = "completed") -> None:
    """Mark a trace as completed or failed."""
    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE traces SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, trace_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_trace(trace_id: str) -> dict | None:
    """Get a trace with all its events."""
    conn = get_db()
    try:
        trace = conn.execute(
            "SELECT * FROM traces WHERE id = ?",
            (trace_id,),
        ).fetchone()
        if trace is None:
            return None

        events = conn.execute(
            "SELECT * FROM trace_events WHERE trace_id = ? ORDER BY id ASC",
            (trace_id,),
        ).fetchall()

        return {
            "id": trace["id"],
            "session_id": trace["session_id"],
            "status": trace["status"],
            "created_at": trace["created_at"],
            "updated_at": trace["updated_at"],
            "events": [
                {
                    "id": e["id"],
                    "event_type": e["event_type"],
                    "step_index": e["step_index"],
                    "data": json.loads(e["data"]),
                    "created_at": e["created_at"],
                }
                for e in events
            ],
        }
    finally:
        conn.close()
