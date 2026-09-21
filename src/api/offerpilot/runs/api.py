"""Run creation, state inspection, cancellation, and SSE transport."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from fastapi.responses import JSONResponse, StreamingResponse

from offerpilot.core.errors import AppError
from offerpilot.diagnosis.repository import get_report
from offerpilot.profiles.cookies import require_profile_id
from offerpilot.runs.contracts import RunRequest, validate_run_input
from offerpilot.runs.events import stream
from offerpilot.approvals.repository import get_approval
from offerpilot.runs.repository import create_run, events_after, get_run, list_runs, request_cancel, transition_run
from offerpilot.runs.calls import list_calls
from offerpilot.runs.service import run_service
from offerpilot.sessions.followups import link_followup
from offerpilot.sessions.guards import require_active_session
from offerpilot.sessions.repository import add_message

router = APIRouter(prefix="/api", tags=["runs"])


class ExportReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    report_id: str


def _owned_run(run_id: str, profile_id: str) -> dict:
    run = get_run(run_id, profile_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def _sse(event: dict) -> str:
    return f"id: {event.get('sequence', '')}\ndata: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _public_approval(approval: dict | None) -> dict | None:
    if approval is None:
        return None
    return {key: value for key, value in approval.items() if key != "params"}


@router.get("/coach/state")
async def coach_state_endpoint(
    session_id: str = Query(..., min_length=1),
    profile_id: str = Depends(require_profile_id),
):
    """Return the durable Coach snapshot used by refresh/restart recovery."""
    require_active_session(session_id, profile_id)
    runs = list_runs(session_id, profile_id, 20)
    run = next((item for item in runs if item["status"] in {"pending", "running", "waiting_approval"}), None)
    if run is None:
        run = next((item for item in runs if item["type"] == "coach"), None)
    if run is None:
        return {"session_id": session_id, "run": None, "trace": [], "approval": None}
    approval_id = str(run.get("state", {}).get("approval_id", ""))
    approval = get_approval(approval_id, profile_id) if approval_id else None
    return {
        "session_id": session_id,
        "run": run,
        "trace": events_after(run["id"], 0, profile_id=profile_id),
        "approval": _public_approval(approval),
    }


@router.post("/sessions/{session_id}/runs")
async def create_run_endpoint(
    session_id: str,
    body: RunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    profile_id: str = Depends(require_profile_id),
):
    require_active_session(session_id, profile_id)
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    input_data = validate_run_input(body.type, body.input)
    try:
        run, reused = create_run(profile_id, session_id, body.type, input_data, idempotency_key)
    except LookupError as exc:
        raise AppError("Session not found", code="session_not_found", status_code=404) from exc
    except RuntimeError as exc:
        raise AppError("A Session already has an active Run", code=str(exc), status_code=409) from exc
    except ValueError as exc:
        raise AppError("Invalid Run input", code=str(exc), status_code=422) from exc
    if not reused:
        if body.type == "coach":
            add_message(
                session_id,
                "user",
                input_data["message"],
                run_id=run["id"],
                kind="coach_input",
                profile_id=profile_id,
            )
        elif body.type == "diagnosis":
            add_message(
                session_id,
                "user",
                f"[Formal diagnosis]\nQuestion: {input_data['question']}\nAnswer: {input_data['answer']}",
                run_id=run["id"],
                kind="diagnosis_input",
                profile_id=profile_id,
            )
            followup_id = input_data.get("followup_id")
            if isinstance(followup_id, str) and not link_followup(
                followup_id, session_id, profile_id, run["id"]
            ):
                transition_run(
                    run["id"],
                    "cancelled",
                    error_code="followup_unavailable",
                    event_type="run_cancelled",
                    event_data={"reason": "followup_unavailable"},
                )
                raise HTTPException(status_code=409, detail="Follow-up is not available")
        run_service.start(run["id"])
    return JSONResponse(status_code=202, content={"run": run, "reused": reused})


@router.post("/coach/reports/export")
async def export_report_endpoint(
    body: ExportReportRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    profile_id: str = Depends(require_profile_id),
):
    """Create an export Run only after validating report ownership."""
    require_active_session(body.session_id, profile_id)
    report = get_report(body.report_id, profile_id)
    if report is None or report["session_id"] != body.session_id:
        raise HTTPException(status_code=404, detail="Report not found")
    key = idempotency_key or f"export:{body.report_id}"
    try:
        run, reused = create_run(
            profile_id,
            body.session_id,
            "report_export",
            {"report_id": body.report_id},
            key,
        )
    except RuntimeError as exc:
        raise AppError("A Session already has an active Run", code=str(exc), status_code=409) from exc
    if not reused:
        run_service.start(run["id"])
    return JSONResponse(status_code=202, content={"run": run, "reused": reused})


@router.get("/runs/{run_id}")
async def get_run_endpoint(run_id: str, profile_id: str = Depends(require_profile_id)):
    return _owned_run(run_id, profile_id)


@router.get("/runs/{run_id}/calls")
async def run_calls_endpoint(run_id: str, profile_id: str = Depends(require_profile_id)):
    _owned_run(run_id, profile_id)
    return {"calls": list_calls(run_id, profile_id) or []}


@router.get("/runs/{run_id}/events")
async def run_events_endpoint(
    run_id: str,
    after: int = Query(0, ge=0),
    profile_id: str = Depends(require_profile_id),
):
    _owned_run(run_id, profile_id)
    return {"events": events_after(run_id, after, profile_id=profile_id)}


@router.get("/runs/{run_id}/stream")
async def run_stream_endpoint(
    run_id: str,
    after: int = Query(0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    profile_id: str = Depends(require_profile_id),
):
    _owned_run(run_id, profile_id)
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))

    async def event_stream():
        async for event in stream(run_id, after, profile_id=profile_id):
            yield ": ping\n\n" if event["type"] == "ping" else _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run_endpoint(run_id: str, profile_id: str = Depends(require_profile_id)):
    run = _owned_run(run_id, profile_id)
    if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
        return {"run": run, "already_terminal": True}
    if not request_cancel(run_id, profile_id):
        raise AppError("Run cannot be cancelled", code="run_cannot_cancel", status_code=409)
    await run_service.cancel_owned_runs(profile_id, session_id=run["session_id"], timeout=0.05)
    return {"run": get_run(run_id, profile_id), "already_terminal": False}
