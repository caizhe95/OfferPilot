"""SQLite persistence for Sessions and their immutable messages."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from typing import Any

from offerpilot.database.connection import get_db
from offerpilot.database.values import dumps, loads, now
from offerpilot.profiles.repository import ensure_profile


def _session_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "profile_id": row["profile_id"],
        "title": row["title"],
        "title_source": row["title_source"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "metadata": loads(row["metadata"], {}),
    }


def create_session(profile_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_profile(profile_id)
    session_id = str(uuid.uuid4())
    timestamp = now()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO sessions(id, profile_id, title, title_source, status, created_at, updated_at, metadata) "
            "VALUES(?, ?, '', 'auto', 'active', ?, ?, ?)",
            (session_id, profile_id, timestamp, timestamp, dumps(metadata or {})),
        )
        conn.commit()
    finally:
        conn.close()
    return get_session(session_id, profile_id) or {}


def get_session(session_id: str, profile_id: str | None = None) -> dict[str, Any] | None:
    conn = get_db()
    try:
        sql = "SELECT * FROM sessions WHERE id = ?"
        params: list[Any] = [session_id]
        if profile_id is not None:
            sql += " AND profile_id = ?"
            params.append(profile_id)
        row = conn.execute(sql, params).fetchone()
        return _session_row(row) if row else None
    finally:
        conn.close()


def _encode_cursor(row: dict[str, Any]) -> str:
    payload = dumps({"updated_at": row["updated_at"], "id": row["id"]}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[str, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        updated_at, session_id = payload["updated_at"], payload["id"]
        if not isinstance(updated_at, str) or not updated_at or not isinstance(session_id, str) or not session_id:
            raise ValueError
        return updated_at, session_id
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ValueError("invalid_cursor") from exc


def list_sessions(
    profile_id: str,
    limit: int = 20,
    cursor: str | None = None,
    status: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    limit = max(1, min(limit, 100))
    conn = get_db()
    try:
        params: list[Any] = [profile_id]
        sql = "SELECT * FROM sessions WHERE profile_id = ?"
        if status in {"active", "archived"}:
            sql += " AND status = ?"
            params.append(status)
        if cursor:
            cursor_updated_at, cursor_id = _decode_cursor(cursor)
            sql += " AND (updated_at < ? OR (updated_at = ? AND id < ?))"
            params.extend([cursor_updated_at, cursor_updated_at, cursor_id])
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(limit + 1)
        rows = conn.execute(sql, params).fetchall()
        items = [_session_row(row) for row in rows[:limit]]
        next_cursor = _encode_cursor(items[-1]) if len(rows) > limit and items else None
        return items, next_cursor
    finally:
        conn.close()


def update_session(
    session_id: str,
    profile_id: str,
    *,
    title: str | None = None,
    archived: bool | None = None,
) -> dict[str, Any] | None:
    session = get_session(session_id, profile_id)
    if session is None:
        return None
    fields: list[str] = []
    params: list[Any] = []
    if title is not None:
        fields.extend(["title = ?", "title_source = 'manual'"])
        params.append(" ".join(title.split())[:120])
    if archived is not None:
        fields.append("status = ?")
        params.append("archived" if archived else "active")
    if fields:
        fields.append("updated_at = ?")
        params.append(now())
        params.extend([session_id, profile_id])
        conn = get_db()
        try:
            conn.execute(
                f"UPDATE sessions SET {', '.join(fields)} WHERE id = ? AND profile_id = ?", params
            )
            conn.commit()
        finally:
            conn.close()
    return get_session(session_id, profile_id)


def delete_session_record(session_id: str, profile_id: str) -> bool:
    conn = get_db()
    try:
        cursor = conn.execute("DELETE FROM sessions WHERE id = ? AND profile_id = ?", (session_id, profile_id))
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def add_message(
    session_id: str,
    role: str,
    content: str,
    *,
    run_id: str | None = None,
    kind: str = "text",
    metadata: dict[str, Any] | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    timestamp = now()
    conn = get_db()
    try:
        session = conn.execute("SELECT profile_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None or (profile_id is not None and session["profile_id"] != profile_id):
            raise LookupError("session_not_found")
        owner = str(session["profile_id"])
        if run_id is not None and conn.execute(
            "SELECT 1 FROM runs WHERE id = ? AND session_id = ? AND profile_id = ?", (run_id, session_id, owner)
        ).fetchone() is None:
            raise LookupError("run_not_found")
        cursor = conn.execute(
            "INSERT INTO messages(session_id, profile_id, run_id, role, kind, content, metadata, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, owner, run_id, role, kind, content, dumps(metadata or {}), timestamp),
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "session_id": session_id,
            "profile_id": owner,
            "run_id": run_id,
            "role": role,
            "kind": kind,
            "content": content,
            "metadata": metadata or {},
            "created_at": timestamp,
        }
    finally:
        conn.close()


def add_audio_transcript(session_id: str, run_id: str, content: str, upload_id: str) -> dict[str, Any]:
    timestamp = now()
    metadata = {"upload_id": upload_id}
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        owner = conn.execute("SELECT profile_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if owner is None or conn.execute(
            "SELECT 1 FROM runs WHERE id = ? AND session_id = ? AND profile_id = ?", (run_id, session_id, owner["profile_id"])
        ).fetchone() is None:
            raise LookupError("run_not_found")
        row = conn.execute(
            "SELECT * FROM messages WHERE run_id = ? AND profile_id = ? AND kind = 'audio_transcript'", (run_id, owner["profile_id"])
        ).fetchone()
        if row is None:
            cursor = conn.execute(
                "INSERT INTO messages(session_id, profile_id, run_id, role, kind, content, metadata, created_at) "
                "VALUES(?, ?, ?, 'user', 'audio_transcript', ?, ?, ?)",
                (session_id, owner["profile_id"], run_id, content, dumps(metadata), timestamp),
            )
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
        conn.commit()
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "profile_id": row["profile_id"],
            "run_id": row["run_id"],
            "role": row["role"],
            "kind": row["kind"],
            "content": row["content"],
            "metadata": loads(row["metadata"], {}),
            "created_at": row["created_at"],
        }
    finally:
        conn.close()


def get_messages(session_id: str, n: int | None = None, profile_id: str | None = None) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        sql = "SELECT * FROM messages WHERE session_id = ?"
        params: list[Any] = [session_id]
        if profile_id is not None:
            sql += " AND profile_id = ?"
            params.append(profile_id)
        sql += " ORDER BY id ASC"
        if n is not None:
            sql = (
                "SELECT * FROM (SELECT * FROM messages WHERE session_id = ?" + (" AND profile_id = ?" if profile_id is not None else "") + " ORDER BY id DESC LIMIT ?) "
                "ORDER BY id ASC"
            )
            params.append(max(1, min(n, 500)))
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "profile_id": row["profile_id"],
                "run_id": row["run_id"],
                "role": row["role"],
                "kind": row["kind"],
                "content": row["content"],
                "metadata": loads(row["metadata"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()
