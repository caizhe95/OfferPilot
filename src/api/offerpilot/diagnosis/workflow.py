"""Formal diagnosis orchestration and durable diagnostic stage events."""

from __future__ import annotations

import asyncio
import math
from time import perf_counter
from typing import Any

from offerpilot.core.deadlines import RunDeadlineExceeded, await_with_deadline, deadline_after, raise_if_cancelled, require_remaining
from offerpilot.core.errors import AppError
from offerpilot.diagnosis.context_builder import build_context
from offerpilot.diagnosis.reporting import build_report, render_and_validate
from offerpilot.diagnosis.repository import save_report
from offerpilot.diagnosis.scoring import build_deterministic_followups, diagnose_interview, select_scorable_points
from offerpilot.knowledge.retrieval import search_knowledge
from offerpilot.llm.structured import LLMInvalidResponseError, LLMOutputTruncatedError
from offerpilot.runs.events import notify
from offerpilot.runs.repository import append_event

DIAGNOSIS_RUN_TIMEOUT_SECONDS = 45.0


def _emit(run_id: str, event_type: str, data: dict[str, Any]) -> None:
    append_event(run_id, event_type, data)
    notify(run_id)


def looks_corrupted_text(text: str) -> bool:
    visible = [character for character in text if not character.isspace()]
    return bool(visible) and sum(character in {"?", "\ufffd"} for character in visible) / len(visible) >= 0.4


def calculate_overall_score(content_scores: dict[str, Any], voice_scores: dict[str, Any]) -> float:
    """Calculate the combined score while rejecting malformed score contracts."""
    total = 0.0
    for name, scores in (("content", content_scores), ("voice", voice_scores)):
        dimension_total = scores.get("total")
        max_total = scores.get("max_total")
        if (
            isinstance(dimension_total, bool)
            or not isinstance(dimension_total, (int, float))
            or isinstance(max_total, bool)
            or not isinstance(max_total, (int, float))
            or max_total <= 0
            or not math.isfinite(float(dimension_total))
            or not math.isfinite(float(max_total))
        ):
            raise AppError(f"{name} score contract is invalid", code="invalid_diagnosis_score", status_code=503)
        total += float(dimension_total) / float(max_total) * 5
    return round(total, 1)


async def run_diagnosis(*, run_id: str, session_id: str, profile_id: str, question: str, answer: str, timeout: float, cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
    if not question.strip() or not answer.strip():
        raise AppError("question and answer are required", code="invalid_diagnosis_input", status_code=422)
    if looks_corrupted_text(question) or looks_corrupted_text(answer):
        raise AppError("question or answer appears corrupted; please submit the original text again", code="invalid_diagnosis_input", status_code=422)
    deadline = deadline_after(min(DIAGNOSIS_RUN_TIMEOUT_SECONDS, max(0.001, timeout)))
    try:
        async with asyncio.timeout(require_remaining(deadline)):
            raise_if_cancelled(cancel_event)
            _emit(run_id, "diagnosis_started", {})
            started = perf_counter()
            knowledge = await search_knowledge(question=question, answer=answer, limit=5, cancel_event=cancel_event, deadline=deadline, run_id=run_id, session_id=session_id, profile_id=profile_id)
            _emit(run_id, "knowledge_merged", {"fts_count": sum(bool(item.get("fts_rank")) for item in knowledge), "vector_count": sum(bool(item.get("vector_rank")) for item in knowledge), "count": len(knowledge), "embedding_unavailable": any(item.get("embedding_unavailable") for item in knowledge), "duration_ms": max(0, int((perf_counter() - started) * 1000))})
            points = select_scorable_points(knowledge)
            if not points: raise AppError("No stable exam points were recalled", code="no_reference_exam_points", status_code=503)
            started = perf_counter()
            context = await await_with_deadline(asyncio.to_thread(build_context, session_id=session_id, profile_id=profile_id, user_input=f"面试题：{question}\n回答：{answer}", knowledge_results=knowledge, max_chars=24000), deadline=deadline, cancel_event=cancel_event)
            _emit(run_id, "context_built", {"duration_ms": max(0, int((perf_counter() - started) * 1000)), "context_chars": len(context)})
            _emit(run_id, "diagnosis_model_started", {"exam_point_count": len(points)})
            try:
                diagnosis = await diagnose_interview(question=question, answer=answer, scorable_points=points, context_instruction=context, timeout=require_remaining(deadline), cancel_event=cancel_event, deadline=deadline, on_progress=lambda data: _emit(run_id, "diagnosis_model_progress", data), on_fallback=lambda data: _emit(run_id, "diagnosis_model_fallback", data), run_id=run_id, session_id=session_id, profile_id=profile_id)
            except (LLMInvalidResponseError, LLMOutputTruncatedError) as exc:
                metrics = dict(getattr(exc, "metrics", {}) or {})
                if metrics: _emit(run_id, "diagnosis_model_completed", metrics)
                _emit(run_id, "output_validated", {"success": False, "duration_ms": int(getattr(exc, "validation_duration_ms", 0) or 0), "error_code": exc.code})
                raise
            metrics = dict(diagnosis.pop("_llm_metrics", {}) or {})
            _emit(run_id, "diagnosis_model_completed", metrics)
            _emit(run_id, "output_validated", {"success": True, "duration_ms": int(diagnosis.pop("_validation_duration_ms", 0) or 0)})
            diagnosis["followups"] = build_deterministic_followups(diagnosis, knowledge)
            content, voice = diagnosis["content_scores"], diagnosis["voice_scores"]
            overall = calculate_overall_score(content, voice)
            markdown, output = render_and_validate(build_report(question, answer, diagnosis, overall, knowledge))
            if not output["valid"]:
                _emit(run_id, "output_check", {"result": "failed", "issues": output["issues"]})
                raise AppError("Diagnosis report failed output validation", code="output_check_failed", status_code=503)
            started = perf_counter()
            raise_if_cancelled(cancel_event)
            saved = save_report(run_id=run_id, session_id=session_id, profile_id=profile_id, question=question, answer=answer, diagnosis=diagnosis, markdown=markdown, overall_score=overall, knowledge=knowledge)
            _emit(run_id, "report_ready", {"report_id": saved["report_id"], "overall_score": saved["overall_score"], "source_count": len(saved["sources"]), "duration_ms": max(0, int((perf_counter() - started) * 1000))})
            return saved
    except asyncio.CancelledError: raise
    except (RunDeadlineExceeded, TimeoutError) as exc: raise AppError("diagnosis timeout", code="diagnosis_timeout", status_code=504) from exc
