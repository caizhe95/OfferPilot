"""Anonymous Profile bootstrap, growth, and reset endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from offerpilot.core.errors import AppError
from offerpilot.profiles.cookies import issue_profile_cookie, parse_profile_cookie
from offerpilot.profiles.growth import get_profile_growth
from offerpilot.profiles.repository import ensure_profile
from offerpilot.profiles.lifecycle import reset_profile_with_assets
from offerpilot.runs.service import run_service

router = APIRouter(prefix="/api/profile", tags=["profile"])


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResetProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str = Field(min_length=1, max_length=32)


@router.post("/bootstrap")
async def bootstrap_profile(body: BootstrapRequest, request: Request, response: Response):
    profile_id = parse_profile_cookie(request.cookies.get("offerpilot_profile")) or str(uuid.uuid4())
    ensure_profile(profile_id)
    issue_profile_cookie(response, profile_id)
    return {"profile_id": profile_id}


@router.get("/growth")
async def profile_growth(request: Request):
    profile_id = parse_profile_cookie(request.cookies.get("offerpilot_profile"))
    if not profile_id:
        raise HTTPException(status_code=401, detail="Missing or invalid offerpilot_profile cookie")
    return get_profile_growth(profile_id)


@router.post("/reset")
async def reset_profile_endpoint(body: ResetProfileRequest, request: Request, response: Response):
    if body.confirmation != "RESET":
        raise HTTPException(status_code=400, detail="Confirmation must be RESET")
    profile_id = parse_profile_cookie(request.cookies.get("offerpilot_profile"))
    if not profile_id:
        raise HTTPException(status_code=401, detail="Missing or invalid offerpilot_profile cookie")
    if not await run_service.cancel_owned_runs(profile_id, timeout=10.0):
        raise AppError("Active Run did not settle before reset", code="active_run_not_settled", status_code=409)
    reset_profile_with_assets(profile_id)
    ensure_profile(profile_id)
    issue_profile_cookie(response, profile_id)
    return {"status": "reset", "profile_id": profile_id}
