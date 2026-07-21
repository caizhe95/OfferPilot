"""Session state machine and management.

Handles session lifecycle: created → running → waiting_approval/paused → completed/failed/cancelled.
"""

import uuid
import json
from datetime import datetime, timezone
from app.core.database import get_db


# Valid state transitions
VALID_TRANSITIONS: dict[str, set[str]] = {
    "created": {"running", "cancelled"},
    "running": {"waiting_approval", "paused", "completed", "failed", "cancelled"},
    "waiting_approval": {"running", "failed", "cancelled"},
    "paused": {"running", "cancelled"},
    "completed": set(),      # terminal
    "failed": set(),         # terminal
    "cancelled": set(),      # terminal
}

VALID_STATUSES = set(VALID_TRANSITIONS.keys())


def create_session(metadata: dict | None = None) -> dict:
    """Create a new session in 'created' state."""
    conn = get_db()
    try:
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        conn.execute(
            "INSERT INTO sessions (id, status, created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?)",
            (session_id, "created", now, now, meta_json),
        )
        conn.commit()
        return {
            "id": session_id,
            "status": "created",
            "created_at": now,
            "updated_at": now,
            "metadata": metadata or {},
        }
    finally:
        conn.close()


def get_session(session_id: str) -> dict | None:
    """Get session by ID."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, status, created_at, updated_at, metadata FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": json.loads(row["metadata"]),
        }
    finally:
        conn.close()


def transition_session(session_id: str, new_status: str) -> dict | None:
    """Transition session to a new status.

    Returns updated session dict, or None if session not found.
    Raises ValueError if transition is invalid.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status}")

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, status, created_at, metadata FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()

        if row is None:
            return None

        current_status = row["status"]
        if new_status not in VALID_TRANSITIONS.get(current_status, set()):
            raise ValueError(
                f"Invalid transition: {current_status} -> {new_status}"
            )

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, session_id),
        )
        conn.commit()

        return {
            "id": session_id,
            "status": new_status,
            "created_at": row["created_at"],
            "updated_at": now,
            "metadata": json.loads(row["metadata"]),
        }
    finally:
        conn.close()


def mark_waiting_approval(
    session_id: str,
    request_id: str,
    tool_name: str,
) -> dict:
    """Move a session into waiting_approval for a pending tool approval."""
    session = get_session(session_id)
    if session is None:
        raise ValueError("Session not found")

    status = session["status"]
    if status == "waiting_approval":
        return session
    if status in {"completed", "failed", "cancelled"}:
        raise ValueError(f"Cannot wait for approval from terminal state: {status}")
    if status == "created":
        transition_session(session_id, "running")

    updated = transition_session(session_id, "waiting_approval")
    if updated is None:
        raise ValueError("Session not found")
    add_progress_event(
        session_id,
        "permission_checked",
        {"request_id": request_id, "tool_name": tool_name, "status": "waiting_approval"},
    )
    return updated


def resume_after_approval(session_id: str) -> dict | None:
    """Resume a session after a permission approval if it is waiting."""
    session = get_session(session_id)
    if session is None:
        return None
    if session["status"] == "waiting_approval":
        return transition_session(session_id, "running")
    return session


def fail_after_denial(session_id: str, reason: str = "permission_denied") -> dict | None:
    """Mark a waiting/running session as failed after a permission denial."""
    session = get_session(session_id)
    if session is None:
        return None
    if session["status"] in {"waiting_approval", "running"}:
        add_progress_event(session_id, "permission_checked", {"status": "denied", "reason": reason})
        return transition_session(session_id, "failed")
    return session


def add_message(session_id: str, role: str, content: str) -> dict:
    """Add a message to a session."""
    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now),
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": now,
        }
    finally:
        conn.close()


def get_recent_messages(session_id: str, n: int = 10) -> list[dict]:
    """Get the most recent N messages for a session."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, session_id, role, content, created_at FROM messages "
            "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, n),
        ).fetchall()
        # Return in chronological order
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]
    finally:
        conn.close()


def add_progress_event(session_id: str, stage: str, metadata: dict | None = None) -> dict:
    """Record a progress event."""
    valid_stages = {
        "input_received", "qa_extracted", "skill_selected",
        "permission_checked", "knowledge_retrieved", "content_scored",
        "voice_scored", "memory_updated", "report_generated",
        "output_checked", "completed",
    }
    if stage not in valid_stages:
        raise ValueError(f"Invalid progress stage: {stage}")

    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        cursor = conn.execute(
            "INSERT INTO progress_events (session_id, stage, metadata, created_at) VALUES (?, ?, ?, ?)",
            (session_id, stage, meta_json, now),
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "session_id": session_id,
            "stage": stage,
            "metadata": metadata or {},
            "created_at": now,
        }
    finally:
        conn.close()


def get_progress_events(session_id: str) -> list[dict]:
    """Get all progress events for a session in order."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, session_id, stage, metadata, created_at FROM progress_events "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "stage": row["stage"],
                "metadata": json.loads(row["metadata"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def save_checkpoint(
    session_id: str,
    state: str,
    progress: list[str] | None = None,
    messages: list[dict] | None = None,
    knowledge: list[str] | None = None,
    memory_keys: list[str] | None = None,
) -> dict:
    """Save a checkpoint for a session."""
    checkpoint_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO checkpoints (id, session_id, state, progress, messages, knowledge, memory_keys, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                checkpoint_id,
                session_id,
                json.dumps(state, ensure_ascii=False),
                json.dumps(progress or [], ensure_ascii=False),
                json.dumps(messages or [], ensure_ascii=False),
                json.dumps(knowledge or [], ensure_ascii=False),
                json.dumps(memory_keys or [], ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
        return {
            "id": checkpoint_id,
            "session_id": session_id,
            "state": state,
            "progress": progress or [],
            "messages": messages or [],
            "knowledge": knowledge or [],
            "memory_keys": memory_keys or [],
            "created_at": now,
        }
    finally:
        conn.close()


def get_checkpoint(checkpoint_id: str) -> dict | None:
    """Retrieve a checkpoint by ID."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE id = ?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "state": json.loads(row["state"]),
            "progress": json.loads(row["progress"]),
            "messages": json.loads(row["messages"]),
            "knowledge": json.loads(row["knowledge"]),
            "memory_keys": json.loads(row["memory_keys"]),
            "created_at": row["created_at"],
        }
    finally:
        conn.close()


def get_latest_checkpoint(session_id: str) -> dict | None:
    """Get the most recent checkpoint for a session."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "state": json.loads(row["state"]),
            "progress": json.loads(row["progress"]),
            "messages": json.loads(row["messages"]),
            "knowledge": json.loads(row["knowledge"]),
            "memory_keys": json.loads(row["memory_keys"]),
            "created_at": row["created_at"],
        }
    finally:
        conn.close()


def list_sessions(status: str | None = None, limit: int = 20) -> list[dict]:
    """List sessions, optionally filtered by status."""
    limit = max(1, min(limit, 100))
    conn = get_db()
    try:
        if status:
            rows = conn.execute(
                "SELECT id, status, created_at, updated_at, metadata FROM sessions "
                "WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, status, created_at, updated_at, metadata FROM sessions "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "metadata": json.loads(row["metadata"]),
            }
            for row in rows
        ]
    finally:
        conn.close()
