"""Signed anonymous profile cookies and profile-owned resource checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid

from fastapi import Header, HTTPException, Request, Response

from offerpilot.core.config import settings

PROFILE_COOKIE = "offerpilot_profile"
PROFILE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


def _signature(profile_id: str, expires_at: int) -> str:
    secret = settings.profile_signing_key.encode("utf-8")
    payload = f"{profile_id}.{expires_at}".encode("ascii")
    digest = hmac.new(secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def make_profile_cookie(profile_id: str, *, expires_at: int | None = None) -> str:
    """Create the complete signed cookie value, including server-verifiable expiry."""
    expiry = expires_at if expires_at is not None else int(time.time()) + PROFILE_MAX_AGE_SECONDS
    return f"{profile_id}.{expiry}.{_signature(profile_id, expiry)}"


def _parse_profile_cookie(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.split(".")
    if len(parts) != 3:
        return None
    profile_id, raw_expiry, signature = parts
    try:
        profile_id = str(uuid.UUID(profile_id))
        expires_at = int(raw_expiry)
    except (TypeError, ValueError, AttributeError):
        return None
    if expires_at < int(time.time()):
        return None
    expected = _signature(profile_id, expires_at)
    return profile_id if hmac.compare_digest(signature, expected) else None


def issue_profile_cookie(response: Response, profile_id: str) -> None:
    response.set_cookie(
        key=PROFILE_COOKIE,
        value=make_profile_cookie(profile_id),
        max_age=PROFILE_MAX_AGE_SECONDS,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        path="/",
    )


def require_profile_id(request: Request) -> str:
    profile_id = _parse_profile_cookie(request.cookies.get(PROFILE_COOKIE))
    if not profile_id:
        raise HTTPException(status_code=401, detail="Missing or invalid offerpilot_profile cookie")
    return profile_id


def require_admin_key(x_offerpilot_admin_key: str | None = Header(default=None)) -> None:
    """Require the separately configured admin key using constant-time comparison."""
    expected = settings.admin_key
    if not expected or not x_offerpilot_admin_key or not hmac.compare_digest(x_offerpilot_admin_key, expected):
        raise HTTPException(status_code=403, detail="Invalid admin key")


def is_trusted_origin(origin: str | None) -> bool:
    if not origin:
        return False
    return origin.rstrip("/") in settings.allowed_origins


def require_owned_session(session: dict | None, profile_id: str) -> dict:
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("profile_id") != profile_id:
        raise HTTPException(status_code=403, detail="Session does not belong to this profile")
    return session
