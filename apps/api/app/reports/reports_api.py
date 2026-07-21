"""Report API compatibility endpoints."""

from fastapi import APIRouter, HTTPException
from app.diagnosis.diagnosis import get_diagnosis_report
from app.core.api_helpers import permission_required_response
from app.permission.permission import permission_gate

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/export")
async def export_report(session_id: str, report_id: str):
    """Export a diagnosis report through the high-risk permission gate."""
    params = {"session_id": session_id, "report_id": report_id}
    permission = permission_gate.check(session_id, "export_report", params)
    if not permission.get("allowed"):
        event = permission_required_response(
            session_id=session_id,
            tool_name="export_report",
            permission_result=permission,
            params=params,
        )
        return event

    report = get_diagnosis_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report
