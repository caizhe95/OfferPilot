"""Trace and Eval API endpoints."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from offerpilot.trace.trace import get_trace
from offerpilot.eval.eval import (
    run_eval,
    run_all_evals,
    save_eval_run,
    EVAL_CASES,
)
from offerpilot.core.profile import require_admin_key, require_owned_session, require_profile_id
from offerpilot.session.session import get_session

router = APIRouter(prefix="/api", tags=["trace-eval"])


@router.get("/traces/{trace_id}")
async def get_trace_endpoint(trace_id: str, profile_id: str = Depends(require_profile_id)):
    """Get a trace by ID with all events."""
    trace = get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    require_owned_session(get_session(trace["session_id"]), profile_id)
    return trace


@router.get("/evals/cases")
async def list_eval_cases(_: None = Depends(require_admin_key)):
    """List all eval case names."""
    return {
        "cases": [{"name": c["name"], "question": c.get("question", "")[:50]} for c in EVAL_CASES],
        "total": len(EVAL_CASES),
    }


@router.post("/evals/run/{case_name}")
async def run_eval_endpoint(case_name: str, _: None = Depends(require_admin_key)):
    """Run a single eval case."""
    result = await asyncio.to_thread(run_eval, case_name)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/evals/run-all")
async def run_all_evals_endpoint(_: None = Depends(require_admin_key)):
    """Run all eval cases."""
    results = await asyncio.to_thread(run_all_evals)
    saved = save_eval_run(results)
    return {
        "run_id": saved["id"],
        "summary": saved["summary"],
        "total": results["total"],
        "passed": results["passed"],
        "failed": results["failed"],
        "pass_rate": results["pass_rate"],
        "results": results["results"],
    }
