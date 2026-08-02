"""Public Coach and deterministic diagnosis SSE endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from offerpilot.agent.coach_loop import CoachLoop
from offerpilot.coaching.runtime import cancel_active_run, register_run, release_run
from offerpilot.coaching.state import (
    begin_coach_resume,
    begin_run,
    claim_approved_approval,
    create_approval,
    finish_approval,
    get_approval,
    get_run,
    list_active_approvals,
    request_cancel,
    save_run,
)
from offerpilot.core.api_helpers import permission_required_response
from offerpilot.core.profile import require_owned_session, require_profile_id
from offerpilot.diagnosis.diagnosis import get_diagnosis_report, persist_diagnosis_terminal
from offerpilot.diagnosis.workflow import run_diagnosis
from offerpilot.permission.permission import get_tool_risk, permission_gate, write_audit_log
from offerpilot.session.session import add_message, create_session, get_session, resume_after_approval, transition_session
from offerpilot.trace.trace_eval import add_trace_event, complete_trace, get_trace

router = APIRouter(prefix="/api", tags=["coach"])


class DiagnosisInput(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(min_length=1, max_length=7000)


class CoachRequest(BaseModel):
    mode: Literal["coach", "diagnosis"] = "coach"
    message: str | None = Field(default=None, min_length=1, max_length=12000)
    diagnosis: DiagnosisInput | None = None
    session_id: str | None = None

    @model_validator(mode="after")
    def validate_mode_payload(self) -> "CoachRequest":
        if self.mode == "coach" and (not self.message or self.diagnosis is not None):
            raise ValueError("coach mode requires message and forbids diagnosis")
        if self.mode == "diagnosis" and (self.diagnosis is None or self.message is not None):
            raise ValueError("diagnosis mode requires diagnosis and forbids message")
        return self


class CoachResumeRequest(BaseModel):
    session_id: str
    request_id: str


class CoachCancelRequest(BaseModel):
    session_id: str
    trace_id: str


class ExportReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    report_id: str


class ExportResumeRequest(ExportReportRequest):
    request_id: str


def _diagnosis_error_code(exc: Exception) -> str:
    category = getattr(exc, "category", "")
    if isinstance(category, str) and category:
        return category
    code = getattr(exc, "code", "")
    if isinstance(code, str) and code:
        return code
    return exc.__class__.__name__.lower()


@router.post("/coach/reports/export")
async def export_report(body: ExportReportRequest, profile_id: str = Depends(require_profile_id)):
    require_owned_session(get_session(body.session_id), profile_id)
    report = get_diagnosis_report(body.report_id)
    if report is None or report["session_id"] != body.session_id:
        raise HTTPException(status_code=404, detail="Report not found")
    params = {"session_id": body.session_id, "report_id": body.report_id}
    policy = permission_gate.check(body.session_id, "export_report", params, profile_id=profile_id)
    if not policy["allowed"]:
        if policy["risk_level"] == "critical":
            raise HTTPException(status_code=403, detail=policy["reason"])
        request_id = create_approval(
            body.session_id,
            profile_id,
            "export_report",
            get_tool_risk("export_report").value,
            params,
            flow_kind="export",
            public_params={"report_id": body.report_id},
        )
        return permission_required_response(
            session_id=body.session_id,
            tool_name="export_report",
            permission_result={"request_id": request_id, "risk_level": "high"},
            params={"report_id": body.report_id},
        )
    return report


@router.post("/coach/reports/export/resume")
async def resume_export_report(body: ExportResumeRequest, profile_id: str = Depends(require_profile_id)):
    require_owned_session(get_session(body.session_id), profile_id)
    approval = get_approval(body.request_id, body.session_id, profile_id)
    if (
        approval is None
        or approval["flow_kind"] != "export"
        or approval["tool_name"] != "export_report"
        or approval["params"].get("report_id") != body.report_id
    ):
        raise HTTPException(status_code=409, detail="Approval does not belong to this export")
    pending = claim_approved_approval(body.request_id, body.session_id, profile_id)
    if pending is None:
        raise HTTPException(status_code=409, detail="Approval is not ready for a single execution")
    report = get_diagnosis_report(body.report_id)
    if report is None or report["session_id"] != body.session_id:
        finish_approval(body.request_id, succeeded=False, error="report not found")
        raise HTTPException(status_code=404, detail="Report not found")
    finish_approval(body.request_id, succeeded=True)
    write_audit_log(body.session_id, "export_report", pending["risk_level"], "execute", pending["public_params"])
    resume_after_approval(body.session_id)
    return {"status": "executed", "report": report}


@router.post("/coach")
async def coach(body: CoachRequest, http_request: Request, profile_id: str = Depends(require_profile_id)):
    session_id = body.session_id or create_session(profile_id)["id"]
    require_owned_session(get_session(session_id), profile_id)
    run = begin_run(session_id, profile_id, body.mode)
    if run is None:
        raise HTTPException(status_code=409, detail="Session is not ready for a new Coach turn")
    trace_id = run["trace_id"]
    cancel_event = register_run(session_id, trace_id)

    if body.mode == "diagnosis":
        assert body.diagnosis is not None
        add_message(session_id, "user", f"[Formal diagnosis]\nQuestion: {body.diagnosis.question}\nAnswer: {body.diagnosis.answer}")
        return _diagnosis_stream(
            http_request=http_request,
            session_id=session_id,
            profile_id=profile_id,
            trace_id=trace_id,
            diagnosis=body.diagnosis,
            cancel_event=cancel_event,
        )

    assert body.message is not None
    add_message(session_id, "user", body.message)
    if _looks_like_scoring_request(body.message):
        return _guidance_stream(http_request, session_id, profile_id, trace_id)
    return _coach_stream(http_request, session_id, profile_id, trace_id, cancel_event)


@router.post("/coach/cancel")
async def cancel_coach(body: CoachCancelRequest, profile_id: str = Depends(require_profile_id)):
    require_owned_session(get_session(body.session_id), profile_id)
    updated = request_cancel(body.session_id, profile_id, body.trace_id)
    cancel_active_run(body.session_id, body.trace_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Active run not found")
    return {"status": "cancel_requested", "session_id": body.session_id, "trace_id": body.trace_id}


@router.get("/coach/state")
async def coach_state(session_id: str, profile_id: str = Depends(require_profile_id)):
    """Return durable recovery data without exposing internal approval parameters."""
    session = require_owned_session(get_session(session_id), profile_id)
    run = get_run(session_id, profile_id)
    trace = get_trace(run["trace_id"]) if run else None
    approvals = [
        {
            "request_id": approval["id"],
            "tool_name": approval["tool_name"],
            "risk_level": approval["risk_level"],
            "flow_kind": approval["flow_kind"],
            "trace_id": approval["trace_id"],
            "status": approval["status"],
            "params": approval["public_params"],
            "created_at": approval["created_at"],
            "expires_at": approval["expires_at"],
        }
        for approval in list_active_approvals(session_id, profile_id)
    ]
    return {"session": session, "run": run, "trace": trace, "approvals": approvals}


def _coach_stream(
    http_request: Request,
    session_id: str,
    profile_id: str,
    trace_id: str,
    cancel_event: asyncio.Event,
) -> StreamingResponse:
    loop = CoachLoop(session_id=session_id, profile_id=profile_id, trace_id=trace_id, cancel_event=cancel_event)

    async def events():
        try:
            async for event in loop.stream():
                if await http_request.is_disconnected():
                    request_cancel(session_id, profile_id, trace_id)
                    cancel_active_run(session_id, trace_id)
                    break
                if event["type"] == "ping":
                    yield ": ping\n\n"
                    continue
                yield _sse(event)
        finally:
            result = loop.result
            if result and not result.waiting_approval:
                current = get_session(session_id)
                if current and current["status"] == "running":
                    transition_session(session_id, "ready")
            release_run(session_id, trace_id)

    return _streaming_response(events())


def _diagnosis_stream(
    *,
    http_request: Request,
    session_id: str,
    profile_id: str,
    trace_id: str,
    diagnosis: DiagnosisInput,
    cancel_event: asyncio.Event,
) -> StreamingResponse:
    async def events():
        sequence = 0
        task: asyncio.Task[dict[str, Any]] | None = None
        terminal_persisted = False

        def event(event_type: str, **data: Any) -> dict[str, Any]:
            nonlocal sequence
            sequence += 1
            return {"type": event_type, "session_id": session_id, "trace_id": trace_id, "sequence": sequence, **data}

        try:
            yield _sse(event("session_start", mode="diagnosis"))
            if await http_request.is_disconnected():
                request_cancel(session_id, profile_id, trace_id)
                cancel_active_run(session_id, trace_id)
                return
            yield _sse(event("diagnosis_started"))
            if await http_request.is_disconnected():
                request_cancel(session_id, profile_id, trace_id)
                cancel_active_run(session_id, trace_id)
                return
            task = asyncio.create_task(
                run_diagnosis(
                    session_id=session_id,
                    profile_id=profile_id,
                    trace_id=trace_id,
                    question=diagnosis.question,
                    answer=diagnosis.answer,
                    timeout=45.0,
                    cancel_event=cancel_event,
                )
            )
            while not task.done():
                await asyncio.wait({task}, timeout=15.0)
                if task.done():
                    break
                yield ": ping\n\n"
                if await http_request.is_disconnected():
                    request_cancel(session_id, profile_id, trace_id)
                    cancel_active_run(session_id, trace_id)
                    task.cancel()
                    break
            result = await task
            terminal_persisted = True
            yield _sse(event("report_ready", report_id=result["report_id"], overall_score=result["overall_score"], sources=result["sources"]))
            yield _sse(event("final_response", content=result["report"], termination_reason="diagnosis_complete"))
            yield _sse(event("run_complete", status="completed", success=True))
        except asyncio.CancelledError:
            persist_diagnosis_terminal(
                session_id=session_id,
                profile_id=profile_id,
                trace_id=trace_id,
                status="cancelled",
                error_code="cancelled",
            )
            terminal_persisted = True
            yield _sse(event("run_complete", status="cancelled", success=False))
        except Exception as exc:
            error_code = _diagnosis_error_code(exc)
            persist_diagnosis_terminal(
                session_id=session_id,
                profile_id=profile_id,
                trace_id=trace_id,
                status="failed",
                error_code=error_code,
            )
            terminal_persisted = True
            yield _sse(event("error", code=error_code, message="Diagnosis could not be completed"))
            yield _sse(event("run_complete", status="failed", success=False))
        finally:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if not terminal_persisted:
                persist_diagnosis_terminal(
                    session_id=session_id,
                    profile_id=profile_id,
                    trace_id=trace_id,
                    status="cancelled",
                    error_code="stream_closed",
                )
            release_run(session_id, trace_id)

    return _streaming_response(events())


def _guidance_stream(
    http_request: Request,
    session_id: str,
    profile_id: str,
    trace_id: str,
) -> StreamingResponse:
    content = "正式评分请切换到正式诊断模式，并分别提交面试题和你的回答；练习对话不会生成非正式分数。"

    async def events():
        completed = False
        try:
            for sequence, (event_type, data) in enumerate(
                (("session_start", {"mode": "coach"}), ("text_delta", {"content": content}), ("final_response", {"content": content, "termination_reason": "formal_diagnosis_required"})),
                start=1,
            ):
                if await http_request.is_disconnected():
                    return
                yield _sse({"type": event_type, "session_id": session_id, "trace_id": trace_id, "sequence": sequence, **data})
            add_message(session_id, "assistant", content)
            save_run(session_id, profile_id, trace_id, "completed", {"run_kind": "coach_guidance"}, run_kind="coach")
            complete_trace(trace_id, "completed")
            transition_session(session_id, "ready")
            completed = True
            yield _sse({"type": "run_complete", "session_id": session_id, "trace_id": trace_id, "sequence": 4, "status": "completed", "success": True})
        finally:
            if not completed:
                save_run(session_id, profile_id, trace_id, "cancelled", {"run_kind": "coach_guidance"}, run_kind="coach")
                complete_trace(trace_id, "cancelled")
                current = get_session(session_id)
                if current and current["status"] == "running":
                    transition_session(session_id, "ready")
            release_run(session_id, trace_id)

    return _streaming_response(events())


@router.post("/coach/resume")
async def resume_coach(body: CoachResumeRequest, http_request: Request, profile_id: str = Depends(require_profile_id)):
    require_owned_session(get_session(body.session_id), profile_id)
    resumed = begin_coach_resume(body.session_id, profile_id, body.request_id)
    if resumed is None:
        raise HTTPException(status_code=409, detail="Approval is not a resumable Coach run")
    saved, approval = resumed
    cancel_event = register_run(body.session_id, saved["trace_id"])
    loop = CoachLoop(session_id=body.session_id, profile_id=profile_id, trace_id=saved["trace_id"], cancel_event=cancel_event)

    async def events():
        try:
            async for event in loop.resume_stream(body.request_id, approval):
                if await http_request.is_disconnected():
                    request_cancel(body.session_id, profile_id, saved["trace_id"])
                    cancel_active_run(body.session_id, saved["trace_id"])
                    break
                if event["type"] == "ping":
                    yield ": ping\n\n"
                    continue
                yield _sse(event)
        finally:
            current = get_session(body.session_id)
            if current and current["status"] == "running":
                transition_session(body.session_id, "ready")
            release_run(body.session_id, saved["trace_id"])

    return _streaming_response(events())


_SCORING_INTENT_TERMS = (
    "评分",
    "打分",
    "几分",
    "多少分",
    "评价",
    "评估",
    "诊断",
    "rate",
    "score",
    "evaluate",
    "evaluation",
    "rating",
)


def _looks_like_scoring_request(message: str) -> bool:
    """Route any formal-score intent to the explicit Diagnosis workflow."""
    normalized = " ".join((message or "").lower().split())
    return any(term in normalized for term in _SCORING_INTENT_TERMS)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _streaming_response(events: Any) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
