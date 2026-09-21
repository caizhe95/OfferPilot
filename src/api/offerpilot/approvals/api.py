"""Approval decision endpoint for paused Runs."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from offerpilot.approvals.repository import get_approval
from offerpilot.approvals.service import decide_approval
from offerpilot.core.errors import AppError
from offerpilot.profiles.cookies import require_profile_id
from offerpilot.runs.repository import get_run
from offerpilot.runs.operation_logs import write_permission_log
from offerpilot.runs.service import run_service

router = APIRouter(prefix="/api", tags=["approvals"])


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "deny"]


def _public_approval(approval: dict) -> dict:
    return {key: value for key, value in approval.items() if key != "params"}


@router.post("/approvals/{approval_id}/decision")
async def approval_decision_endpoint(
    approval_id: str,
    body: ApprovalDecisionRequest,
    profile_id: str = Depends(require_profile_id),
):
    approval = get_approval(approval_id, profile_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    try:
        resolved = decide_approval(approval_id, profile_id, body.decision)
    except (RuntimeError, ValueError) as exc:
        code = str(exc)
        raise AppError("Approval cannot accept this decision", code=code, status_code=409) from exc
    if resolved is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if not resolved.get("decision_reused"):
        write_permission_log(
            resolved["session_id"],
            resolved["tool_name"],
            resolved["risk_level"],
            "approve" if body.decision == "approve" else "deny",
            profile_id=profile_id,
            run_id=resolved["run_id"],
        )
    active_run = get_run(resolved["run_id"], profile_id)
    if active_run is None or active_run["status"] in {
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    }:
        return {"approval": _public_approval(resolved), "run": active_run}
    run_service.start(resolved["run_id"])
    return {
        "approval": _public_approval(resolved),
        "run": get_run(resolved["run_id"], profile_id),
    }
