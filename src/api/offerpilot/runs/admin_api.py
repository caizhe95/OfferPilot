"""Admin-only operational inspection endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query

from offerpilot.core.security import require_admin_key
from offerpilot.runs.operation_logs import list_operation_logs
from offerpilot.runs.repository import events_after, get_run, list_admin_runs

router = APIRouter(prefix="/api/admin", tags=["admin-runtime"])


@router.get("/runs")
async def admin_runs(status: str | None = None, limit: int = Query(100, ge=1, le=500), _: None = Depends(require_admin_key)):
    return {"runs": list_admin_runs(status, limit)}


@router.get("/runs/{run_id}/events")
async def admin_run_events(run_id: str, after: int = Query(0, ge=0), _: None = Depends(require_admin_key)):
    if get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"events": events_after(run_id, after)}


@router.get("/operation-logs")
async def admin_operation_logs(limit: int = Query(100, ge=1, le=500), _: None = Depends(require_admin_key)):
    return {"logs": list_operation_logs(limit)}
