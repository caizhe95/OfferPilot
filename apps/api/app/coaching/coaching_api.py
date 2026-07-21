"""The single public, persistent Coach SSE endpoint."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.coach_loop import CoachLoop
from app.core.profile import require_owned_session, require_profile_id
from app.session.session import add_message, create_session, get_session, transition_session
from app.diagnosis.diagnosis import get_diagnosis_report
from app.core.api_helpers import permission_required_response
from app.permission.permission import permission_gate
from app.trace.trace_eval import create_trace
from app.coaching.state import get_approval

router = APIRouter(prefix="/api", tags=["coach"])


class CoachRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    session_id: str | None = None


class CoachResumeRequest(BaseModel):
    session_id: str
    request_id: str


@router.get("/coach/reports/export")
async def export_report(session_id: str, report_id: str, profile_id: str = Depends(require_profile_id)):
    require_owned_session(get_session(session_id), profile_id)
    params = {"session_id": session_id, "report_id": report_id}
    permission = permission_gate.check(session_id, "export_report", params)
    if not permission.get("allowed"):
        return permission_required_response(session_id=session_id, tool_name="export_report", permission_result=permission, params=params)
    report = get_diagnosis_report(report_id)
    if report is None or report["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.post("/coach")
async def coach(request: CoachRequest, profile_id: str = Depends(require_profile_id)):
    session_id = request.session_id or create_session(profile_id)["id"]
    session = require_owned_session(get_session(session_id), profile_id)
    if session["status"] != "ready":
        raise HTTPException(status_code=409, detail="Session is not ready for a new Coach turn")
    transition_session(session_id, "running")
    add_message(session_id, "user", request.message)
    trace_id = create_trace(session_id)["id"]

    async def events():
        result = await CoachLoop(session_id=session_id, profile_id=profile_id, trace_id=trace_id).run()
        if not result.waiting_approval:
            current = get_session(session_id)
            if current and current["status"] == "running":
                transition_session(session_id, "ready" if result.success else "failed")
        for event in result.events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/coach/resume")
async def resume_coach(request: CoachResumeRequest, profile_id: str = Depends(require_profile_id)):
    session = require_owned_session(get_session(request.session_id), profile_id)
    if session["status"] != "waiting_approval":
        raise HTTPException(status_code=409, detail="Session is not waiting for approval")
    approval = get_approval(request.request_id, request.session_id, profile_id)
    if approval is None or approval["status"] not in {"approved", "denied"}:
        raise HTTPException(status_code=409, detail="Approval has not been resolved")
    transition_session(request.session_id, "running")

    async def events():
        result = await CoachLoop(session_id=request.session_id, profile_id=profile_id, trace_id="").resume(request.request_id, approval)
        current = get_session(request.session_id)
        if current and current["status"] == "running":
            transition_session(request.session_id, "ready" if result.success else "failed")
        for event in result.events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
