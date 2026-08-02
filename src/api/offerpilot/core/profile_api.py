"""Anonymous profile bootstrap endpoints for the signed cookie contract."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from offerpilot.core.config import settings
from offerpilot.core.profile import _parse_profile_cookie, is_trusted_origin, issue_profile_cookie

router = APIRouter(prefix="/api/profile", tags=["profile"])


class BootstrapRequest(BaseModel):
    legacy_profile_id: str | None = None


@router.post("/bootstrap")
async def bootstrap_profile(body: BootstrapRequest, request: Request, response: Response):
    profile_id = _parse_profile_cookie(request.cookies.get("offerpilot_profile"))
    migrated = False
    if profile_id is None and body.legacy_profile_id:
        if not settings.allow_legacy_profile_bootstrap or not is_trusted_origin(request.headers.get("origin")):
            raise HTTPException(status_code=403, detail="Legacy profile bootstrap is disabled")
        try:
            profile_id = str(uuid.UUID(body.legacy_profile_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid legacy profile id") from exc
        migrated = True
    profile_id = profile_id or str(uuid.uuid4())
    issue_profile_cookie(response, profile_id)
    return {"profile_id": profile_id, "migrated": migrated}


@router.get("")
async def get_profile(request: Request):
    profile_id = _parse_profile_cookie(request.cookies.get("offerpilot_profile"))
    if not profile_id:
        raise HTTPException(status_code=401, detail="Missing or invalid offerpilot_profile cookie")
    return {"profile_id": profile_id}
