"""Reusable HTTP-facing ownership checks for Session-scoped endpoints."""

from __future__ import annotations

from fastapi import HTTPException

from offerpilot.core.errors import AppError
from offerpilot.sessions.repository import get_session


def require_owned_session(session_id: str, profile_id: str) -> dict:
    session = get_session(session_id, profile_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def require_active_session(session_id: str, profile_id: str) -> dict:
    session = require_owned_session(session_id, profile_id)
    if session["status"] != "active":
        raise AppError("Archived session is read-only", code="session_archived_read_only", status_code=409)
    return session
