"""Approval decision endpoints.

Approvals are created and executed only by their owning Coach, Audio, or Export
workflow. This router deliberately has no generic tool creation or execution
endpoint, because such endpoints turn untrusted request data into side effects.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from offerpilot.audio.audio import finalize_audio_upload
from offerpilot.coaching.state import get_approval, get_run, grant_permission, resolve_approval
from offerpilot.core.profile import require_owned_session, require_profile_id
from offerpilot.permission.permission import get_audit_logs, write_audit_log
from offerpilot.session.session import get_session, resume_after_approval

router = APIRouter(prefix="/api/permission", tags=["permission"])


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    session_id: str


def _require_approval(request: ApprovalRequest, profile_id: str) -> dict:
    require_owned_session(get_session(request.session_id), profile_id)
    approval = get_approval(request.request_id, request.session_id, profile_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return approval


def _coach_resume_required(approval: dict, profile_id: str) -> bool:
    if approval["flow_kind"] != "coach":
        return False
    run = get_run(approval["session_id"], profile_id)
    return bool(
        run
        and run["status"] == "waiting_approval"
        and run["trace_id"] == approval["trace_id"]
        and run["state"].get("request_id") == approval["id"]
    )


@router.post("/approve")
async def approve_tool(request: ApprovalRequest, profile_id: str = Depends(require_profile_id)):
    approval = _require_approval(request, profile_id)
    resolved = resolve_approval(request.request_id, request.session_id, profile_id, "approved")
    if resolved is None:
        raise HTTPException(status_code=409, detail="Approval already resolved or expired")
    if approval["status"] == "pending" and resolved["risk_level"] == "medium":
        grant_permission(request.session_id, profile_id, resolved["tool_name"])
    if approval["status"] == "pending":
        write_audit_log(
            request.session_id,
            resolved["tool_name"],
            resolved["risk_level"],
            "approve",
            resolved["public_params"],
        )
    return {
        "status": "approved",
        "tool_name": resolved["tool_name"],
        "flow_kind": resolved["flow_kind"],
        "coach_resume_required": _coach_resume_required(approval, profile_id),
    }


@router.post("/deny")
async def deny_tool(request: ApprovalRequest, profile_id: str = Depends(require_profile_id)):
    approval = _require_approval(request, profile_id)
    resolved = resolve_approval(request.request_id, request.session_id, profile_id, "denied")
    if resolved is None:
        raise HTTPException(status_code=409, detail="Approval already resolved or expired")
    if approval["status"] == "pending" and resolved["flow_kind"] == "audio":
        upload_id = resolved["params"].get("upload_id")
        if not isinstance(upload_id, str):
            raise HTTPException(status_code=400, detail="Audio approval has no managed upload")
        if not finalize_audio_upload(upload_id, request.session_id, profile_id, "denied", error="approval denied"):
            raise HTTPException(status_code=400, detail="Audio approval has no managed upload")
    if approval["status"] == "pending":
        write_audit_log(
            request.session_id,
            resolved["tool_name"],
            resolved["risk_level"],
            "deny",
            resolved["public_params"],
        )
    coach_resume_required = _coach_resume_required(approval, profile_id)
    if not coach_resume_required:
        resume_after_approval(request.session_id)
    return {
        "status": "denied",
        "tool_name": resolved["tool_name"],
        "flow_kind": resolved["flow_kind"],
        "coach_resume_required": coach_resume_required,
    }


@router.get("/audit/{session_id}")
async def get_session_audit_logs(session_id: str, limit: int = 50, profile_id: str = Depends(require_profile_id)):
    require_owned_session(get_session(session_id), profile_id)
    return {"audit_logs": get_audit_logs(session_id, limit=limit)}


@router.get("/tools")
async def get_tool_risk_levels():
    from offerpilot.permission.permission import TOOL_RISK_MAP

    return {"tools": [{"name": name, "risk_level": level.value} for name, level in TOOL_RISK_MAP.items()]}
