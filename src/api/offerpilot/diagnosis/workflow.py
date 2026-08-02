"""Deterministic, evidence-grounded diagnosis composite tool."""

from __future__ import annotations

import asyncio
from typing import Any

from offerpilot.core.deadline import RunDeadlineExceeded, await_with_deadline, deadline_after, raise_if_cancelled, require_remaining
from offerpilot.core.errors import AppError
from offerpilot.diagnosis.context_builder import build_context
from offerpilot.diagnosis.diagnosis import diagnose_interview, persist_completed_diagnosis
from offerpilot.knowledge.knowledge_importer import search_knowledge_safe_async
from offerpilot.trace.trace_eval import add_trace_event
from offerpilot.harness.harness import HarnessRunner


def _looks_corrupted_text(text: str) -> bool:
    """Reject text that has been replaced mostly by encoding placeholders."""
    visible = [character for character in text if not character.isspace()]
    if not visible:
        return False
    placeholder_count = sum(character in {"?", "\ufffd"} for character in visible)
    return placeholder_count / len(visible) >= 0.4


async def run_diagnosis(
    *,
    session_id: str,
    profile_id: str,
    trace_id: str,
    question: str,
    answer: str,
    timeout: float,
    cancel_event: Any | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Run the only formal scoring path: retrieval, one score call, validation and persistence."""
    if not question.strip() or not answer.strip():
        raise AppError("question and answer are required", code="invalid_diagnosis_input", status_code=422)
    if _looks_corrupted_text(question) or _looks_corrupted_text(answer):
        raise AppError(
            "question or answer appears corrupted; please submit the original text again",
            code="invalid_diagnosis_input",
            status_code=422,
        )
    run_deadline = min(deadline or float("inf"), deadline_after(min(45.0, max(0.001, timeout))))
    try:
        async with asyncio.timeout(require_remaining(run_deadline)):
            raise_if_cancelled(cancel_event)
            knowledge = await search_knowledge_safe_async(
                question=question,
                answer=answer,
                limit=5,
                trace_id=trace_id,
                cancel_event=cancel_event,
                deadline=run_deadline,
            )
            add_trace_event(trace_id, "knowledge_retrieved", 1, {"count": len(knowledge)})
            add_trace_event(trace_id, "rrf_fused", 1, {"knowledge_ids": [item.get("id") for item in knowledge]})
            context = await await_with_deadline(
                asyncio.to_thread(
                    build_context,
                    session_id=session_id,
                    profile_id=profile_id,
                    user_input=f"面试题：{question}\n回答：{answer}",
                    knowledge_results=knowledge,
                ),
                deadline=run_deadline,
                cancel_event=cancel_event,
            )
            diagnosis = await diagnose_interview(
                session_id=session_id,
                profile_id=profile_id,
                question=question,
                answer=answer,
                knowledge_context=knowledge,
                context_instruction=context,
                timeout=require_remaining(run_deadline),
                cancel_event=cancel_event,
                deadline=run_deadline,
            )
            content = diagnosis["content_scores"]
            voice = diagnosis["voice_scores"]
            overall = round(((content["total"] / content["max_total"]) * 5 + (voice["total"] / voice["max_total"]) * 5), 1)
            report_input = _report_structure(question, answer, diagnosis, overall, knowledge)
            report, output_check = HarnessRunner(session_id).post_output(report_input)
            if not output_check.get("valid"):
                add_trace_event(trace_id, "output_check", 3, {"result": "failed", "issues": output_check.get("issues", [])})
                raise AppError("Diagnosis report failed output validation", code="output_check_failed", status_code=503)
            raise_if_cancelled(cancel_event)
            sources = [str(item.get("source", item.get("title", "")))[:300] for item in knowledge]
            saved = persist_completed_diagnosis(
                session_id=session_id,
                profile_id=profile_id,
                trace_id=trace_id,
                question=question,
                answer=answer,
                content_scores=content,
                voice_scores=voice,
                overall_score=overall,
                report_markdown=report,
                diagnosis=diagnosis,
                sources=sources,
            )
            if saved is None:
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError()
                raise AppError("Diagnosis run is no longer active", code="diagnosis_not_active", status_code=409)
            return {
                "report_id": saved["id"],
                "overall_score": overall,
                "report": report,
                "diagnosis": diagnosis,
                "followups": diagnosis["followups"],
                "memory_candidates": diagnosis["memory_candidates"],
                "sources": sources,
            }
    except asyncio.CancelledError:
        raise
    except (RunDeadlineExceeded, TimeoutError) as exc:
        raise AppError("diagnosis timeout", code="diagnosis_timeout", status_code=504) from exc


def _report_structure(question: str, answer: str, diagnosis: dict[str, Any], overall: float, knowledge: list[dict[str, Any]]) -> dict[str, Any]:
    points = diagnosis["exam_points"]
    return {
        "question": question, "answer": answer, "overall_score": overall,
        "content_scores": diagnosis["content_scores"], "voice_scores": diagnosis["voice_scores"],
        "followups": diagnosis["followups"], "sources": [item.get("source", item.get("title", "")) for item in knowledge],
        "exam_points": points,
        "user_covered": [item for item in points if item["status"] == "covered"],
        "user_missing": [item for item in points if item["status"] != "covered"],
        "improvements": diagnosis["improvements"] or ["请补充具体工程场景和边界条件。"],
        "reference_alignment": "\n".join(f"- {item.get('title', '')}" for item in knowledge[:5]) or "- 未命中参考资料",
    }
