"""Session history, messages, summaries, follow-ups, and report endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from offerpilot.core.errors import AppError
from offerpilot.diagnosis.repository import get_report, list_reports
from offerpilot.profiles.cookies import require_profile_id
from offerpilot.runs.repository import list_runs
from offerpilot.runs.service import run_service
from offerpilot.sessions.followups import list_followups
from offerpilot.sessions.guards import require_owned_session
from offerpilot.sessions.repository import create_session, get_messages, list_sessions, update_session
from offerpilot.sessions.lifecycle import delete_session_with_assets
from offerpilot.sessions.summaries import get_session_summary

router = APIRouter(prefix="/api", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata: dict[str, Any] | None = None


class UpdateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=120)
    archived: bool | None = None


@router.post("/sessions")
async def create_session_endpoint(
    body: CreateSessionRequest, profile_id: str = Depends(require_profile_id)
):
    return create_session(profile_id, body.metadata)


@router.get("/sessions")
async def list_sessions_endpoint(
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    status: str | None = Query(default=None, pattern="^(active|archived|all)$"),
    profile_id: str = Depends(require_profile_id),
):
    try:
        items, next_cursor = list_sessions(
            profile_id, limit, cursor, None if status in {None, "all"} else status
        )
    except ValueError as exc:
        if str(exc) == "invalid_cursor":
            raise AppError("Invalid session cursor", code="invalid_cursor", status_code=400) from exc
        raise
    return {"sessions": items, "next_cursor": next_cursor}


@router.get("/sessions/{session_id}")
async def get_session_endpoint(session_id: str, profile_id: str = Depends(require_profile_id)):
    return require_owned_session(session_id, profile_id)


@router.patch("/sessions/{session_id}")
async def update_session_endpoint(
    session_id: str,
    body: UpdateSessionRequest,
    profile_id: str = Depends(require_profile_id),
):
    updated = update_session(session_id, profile_id, title=body.title, archived=body.archived)
    if updated is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return updated


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session_endpoint(session_id: str, profile_id: str = Depends(require_profile_id)):
    require_owned_session(session_id, profile_id)
    if not await run_service.cancel_owned_runs(profile_id, session_id=session_id, timeout=10.0):
        raise AppError("Active Run did not settle before deletion", code="active_run_not_settled", status_code=409)
    if not delete_session_with_assets(session_id, profile_id):
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/sessions/{session_id}/messages")
async def get_messages_endpoint(
    session_id: str,
    n: int = Query(100, ge=1, le=500),
    profile_id: str = Depends(require_profile_id),
):
    require_owned_session(session_id, profile_id)
    return {"messages": get_messages(session_id, n=n)}


@router.get("/sessions/{session_id}/runs")
async def get_session_runs_endpoint(session_id: str, profile_id: str = Depends(require_profile_id)):
    require_owned_session(session_id, profile_id)
    return {"runs": list_runs(session_id, profile_id, 100)}


@router.get("/sessions/{session_id}/summary")
async def get_summary_endpoint(session_id: str, profile_id: str = Depends(require_profile_id)):
    require_owned_session(session_id, profile_id)
    return get_session_summary(session_id) or {
        "session_id": session_id,
        "summary_version": 0,
        "summary_json": {},
    }


@router.get("/sessions/{session_id}/followups")
async def get_followups_endpoint(session_id: str, profile_id: str = Depends(require_profile_id)):
    require_owned_session(session_id, profile_id)
    return {"followups": list_followups(session_id, profile_id)}


@router.get("/sessions/{session_id}/reports")
async def get_reports_endpoint(session_id: str, profile_id: str = Depends(require_profile_id)):
    require_owned_session(session_id, profile_id)
    return {"reports": list_reports(session_id, profile_id)}


@router.get("/sessions/{session_id}/reports/{report_id}")
async def get_report_endpoint(
    session_id: str,
    report_id: str,
    profile_id: str = Depends(require_profile_id),
):
    require_owned_session(session_id, profile_id)
    report = get_report(report_id, profile_id)
    if report is None or report["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="Report not found")
    return report
