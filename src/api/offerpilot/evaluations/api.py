"""Admin-only evaluation endpoints."""

from fastapi import APIRouter, Depends

from offerpilot.core.security import require_admin_key
from offerpilot.evaluations.runner import run_all_evals

router = APIRouter(prefix="/api/admin/evals", tags=["evals"])


@router.post("/run-all")
async def run_all_evals_endpoint(_: None = Depends(require_admin_key)):
    results = await run_all_evals()
    return {
        "summary": f"Passed: {results['passed']}/{results['total']} ({results['pass_rate']:.0%})",
        **results,
    }
