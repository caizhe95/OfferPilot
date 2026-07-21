"""Agent tool API endpoints.

Provides HTTP endpoints for all tools that the TS Agent can call:
- score_answer
- analyze_voice_text
- generate_followup
- save_memory
- export_report

All tools go through PermissionGate before execution.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.api_helpers import run_guarded_tool
from app.diagnosis.diagnosis import (
    score_answer,
    analyze_voice_text,
    generate_followup,
    save_memory,
    get_diagnosis_report,
)

router = APIRouter(prefix="/api/tools", tags=["agent-tools"])


class ScoreAnswerRequest(BaseModel):
    session_id: str = "default"
    question: str
    answer: str
    knowledge_context: list[dict] | None = None
    trace_id: str | None = None


class AnalyzeVoiceRequest(BaseModel):
    session_id: str = "default"
    transcript: str
    trace_id: str | None = None


class GenerateFollowupRequest(BaseModel):
    session_id: str = "default"
    question: str
    answer: str
    weaknesses: list[str] | None = None
    trace_id: str | None = None


class SaveMemoryRequest(BaseModel):
    session_id: str
    key: str
    value: str
    category: str = "general"
    trace_id: str | None = None


@router.post("/score-answer")
async def score_answer_endpoint(req: ScoreAnswerRequest):
    """Score an interview answer on content dimensions (LOW risk)."""
    params = req.model_dump()
    return run_guarded_tool(
        session_id=req.session_id,
        tool_name="score_answer",
        params=params,
        trace_id=req.trace_id,
        execute=lambda: score_answer(req.question, req.answer, req.knowledge_context),
        trace_payload=lambda result: {
            "tool": "score_answer",
            "result_summary": f"total={result.get('total')}/{result.get('max_total')}",
        },
    )


@router.post("/analyze-voice-text")
async def analyze_voice_endpoint(req: AnalyzeVoiceRequest):
    """Analyze voice dimensions from transcript text (LOW risk)."""
    params = req.model_dump()
    return run_guarded_tool(
        session_id=req.session_id,
        tool_name="analyze_voice_text",
        params=params,
        trace_id=req.trace_id,
        execute=lambda: analyze_voice_text(req.transcript),
        trace_payload=lambda result: {
            "tool": "analyze_voice_text",
            "result_summary": f"total={result.get('total')}/{result.get('max_total')}",
        },
    )


@router.post("/generate-followup")
async def generate_followup_endpoint(req: GenerateFollowupRequest):
    """Generate follow-up questions (LOW risk)."""
    params = req.model_dump()
    return run_guarded_tool(
        session_id=req.session_id,
        tool_name="generate_followup",
        params=params,
        trace_id=req.trace_id,
        execute=lambda: _followup_result(req),
        trace_payload=lambda result: {
            "tool": "generate_followup",
            "result_summary": f"generated={len(result.get('followups', []))} followups",
        },
    )


def _followup_result(req: GenerateFollowupRequest) -> dict:
    followups = generate_followup(req.question, req.answer, req.weaknesses)
    return {"followups": followups, "count": len(followups)}


@router.post("/save-memory")
async def save_memory_endpoint(req: SaveMemoryRequest):
    """Save a memory entry (HIGH risk - requires permission confirmation)."""
    params = req.model_dump()
    return run_guarded_tool(
        session_id=req.session_id,
        tool_name="save_memory",
        params=params,
        trace_id=req.trace_id,
        execute=lambda: save_memory(req.session_id, req.key, req.value, req.category),
        trace_payload=lambda _result: {"tool": "save_memory", "key": req.key},
    )


@router.get("/export-report")
async def export_report_endpoint(session_id: str = "default", report_id: str = "", trace_id: str | None = None):
    """Export a diagnosis report (HIGH risk)."""
    params = {"session_id": session_id, "report_id": report_id, "trace_id": trace_id}
    def execute():
        if not report_id:
            raise HTTPException(status_code=400, detail="report_id is required")
        report = get_diagnosis_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        return report

    return run_guarded_tool(
        session_id=session_id,
        tool_name="export_report",
        params=params,
        trace_id=trace_id,
        execute=execute,
        trace_payload=lambda _result: {"tool": "export_report", "report_id": report_id},
    )
