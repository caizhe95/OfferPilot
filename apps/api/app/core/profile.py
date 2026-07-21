"""Anonymous browser profile helpers and session ownership checks."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Header, HTTPException


PROFILE_HEADER = "X-OfferPilot-Profile-Id"


def require_profile_id(
    profile_id: Annotated[str | None, Header(alias=PROFILE_HEADER)] = None,
) -> str:
    """Read and validate the anonymous browser profile identifier."""
    if not profile_id:
        raise HTTPException(status_code=400, detail=f"Missing {PROFILE_HEADER} header")
    try:
        return str(uuid.UUID(profile_id))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {PROFILE_HEADER} header") from exc


def require_owned_session(session: dict | None, profile_id: str) -> dict:
    """Return a session only when it belongs to the request profile."""
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("profile_id") != profile_id:
        raise HTTPException(status_code=403, detail="Session does not belong to this profile")
    return session
