"""Permission API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.permission.permission import (
    permission_gate,
    write_audit_log,
    get_audit_logs,
    RiskLevel,
)
from app.session.session import fail_after_denial, resume_after_approval

router = APIRouter(prefix="/api/permission", tags=["permission"])
alias_router = APIRouter(prefix="/api/permissions", tags=["permission-alias"])


class CheckPermissionRequest(BaseModel):
    session_id: str
    tool_name: str
    params: dict | None = None


class ApprovalRequest(BaseModel):
    request_id: str
    session_id: str


@router.post("/check")
async def check_permission(request: CheckPermissionRequest):
    """Check if a tool call needs approval."""
    result = permission_gate.check(
        request.session_id,
        request.tool_name,
        request.params,
    )
    # Log the check in audit
    if result.get("request_id"):
        write_audit_log(
            session_id=request.session_id,
            tool_name=request.tool_name,
            risk_level=result["risk_level"],
            action="request",
            params=request.params,
        )
    return result


@router.post("/approve")
async def approve_tool(request: ApprovalRequest):
    """Approve a pending tool request."""
    pending = permission_gate.approve(request.request_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="Pending request not found")

    write_audit_log(
        session_id=request.session_id,
        tool_name=pending["tool_name"],
        risk_level=pending["risk_level"],
        action="approve",
        params=pending.get("params"),
    )
    resume_after_approval(request.session_id)
    return {"status": "approved", "tool_name": pending["tool_name"]}


@router.post("/deny")
async def deny_tool(request: ApprovalRequest):
    """Deny a pending tool request."""
    pending = permission_gate.deny(request.request_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="Pending request not found")

    write_audit_log(
        session_id=request.session_id,
        tool_name=pending["tool_name"],
        risk_level=pending["risk_level"],
        action="deny",
        params=pending.get("params"),
    )
    if pending["tool_name"] == "transcribe_audio":
        from pathlib import Path
        filepath = pending.get("params", {}).get("filepath")
        if filepath:
            Path(filepath).unlink(missing_ok=True)
    fail_after_denial(request.session_id)
    return {"status": "denied", "tool_name": pending["tool_name"]}


class ResumeRequest(BaseModel):
    request_id: str
    session_id: str


@router.post("/resume")
async def resume_tool(request: ResumeRequest):
    """Resume execution of an approved tool call.

    Checks that the request has been approved, then executes the tool.
    Returns the tool result.
    """
    pending = permission_gate.consume_approved_params(request.request_id)
    if not pending:
        raise HTTPException(status_code=404, detail="Pending request not found or not yet approved")

    # Execute the tool
    tool_name = pending["tool_name"]
    params = pending.get("params", {})

    try:
        if tool_name == "save_memory":
            from app.diagnosis.diagnosis import save_memory
            result = save_memory(
                request.session_id,
                params.get("key", "unknown"),
                params.get("value", ""),
                params.get("category", "general"),
            )
        elif tool_name == "export_report":
            from app.diagnosis.diagnosis import get_diagnosis_report
            result = get_diagnosis_report(params.get("report_id", ""))
        elif tool_name == "transcribe_audio":
            from app.audio.audio import transcribe_audio, save_transcript_to_session
            filepath = params.get("filepath")
            if not filepath:
                raise HTTPException(status_code=400, detail="Missing audio filepath")
            transcription = await transcribe_audio(filepath)
            if "error" in transcription:
                return {
                    "status": "asr_failed",
                    "tool_name": tool_name,
                    "error": transcription["error"],
                    "provider": transcription.get("provider"),
                }
            saved = save_transcript_to_session(
                request.session_id,
                transcription["transcript"],
                filepath,
            )
            result = {
                "status": "transcribed",
                "transcript": transcription["transcript"],
                "provider": transcription["provider"],
                "duration_seconds": transcription.get("duration_seconds", 0),
                "language": transcription.get("language", "unknown"),
                "saved": saved,
            }
        else:
            raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")

        write_audit_log(
            session_id=request.session_id,
            tool_name=tool_name,
            risk_level=pending["risk_level"],
            action="execute",
            params=params,
            result=str(result)[:500],
        )
        resume_after_approval(request.session_id)
        if tool_name == "transcribe_audio":
            return result
        return {"status": "executed", "tool_name": tool_name, "result": result}
    except Exception as e:
        return {"status": "error", "tool_name": tool_name, "error": str(e)}


@router.get("/audit/{session_id}")
async def get_session_audit_logs(
    session_id: str,
    limit: int = 50,
):
    """Get audit logs for a session."""
    return {"audit_logs": get_audit_logs(session_id, limit=limit)}


@router.get("/tools")
async def get_tool_risk_levels():
    """List all registered tools and their risk levels."""
    from app.permission.permission import TOOL_RISK_MAP
    return {
        "tools": [
            {"name": name, "risk_level": level.value}
            for name, level in TOOL_RISK_MAP.items()
        ]
    }


@alias_router.post("/check")
async def check_permission_alias(request: CheckPermissionRequest):
    return await check_permission(request)


@alias_router.post("/approve")
async def approve_tool_alias(request: ApprovalRequest):
    return await approve_tool(request)


@alias_router.post("/deny")
async def deny_tool_alias(request: ApprovalRequest):
    return await deny_tool(request)


@alias_router.post("/resume")
async def resume_tool_alias(request: ResumeRequest):
    return await resume_tool(request)
