"""Request-origin and administrator-key guards."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from offerpilot.core.config import settings


def require_admin_key(x_offerpilot_admin_key: str | None = Header(default=None)) -> None:
    """Require the separately configured administrator key."""
    expected = settings.admin_key
    if not expected or not x_offerpilot_admin_key or not hmac.compare_digest(x_offerpilot_admin_key, expected):
        raise HTTPException(status_code=403, detail="Invalid admin key")


def is_trusted_origin(origin: str | None) -> bool:
    """Accept only an explicit configured browser origin for state changes."""
    return bool(origin and origin.rstrip("/") in settings.allowed_origins)
