"""Deterministic, evidence-grounded diagnosis composite tool."""

from __future__ import annotations

import time
from typing import Any

from app.core.errors import AppError
from app.diagnosis.context_builder import build_context
from app.diagnosis.diagnosis import diagnose_interview, save_diagnosis_report
from app.knowledge.knowledge_importer import search_knowledge_safe
from app.trace.trace_eval import add_trace_event
from app.harness.harness import HarnessRunner


def _looks_corrupted_text(text: str) -> bool:
    """Reject text that has been replaced mostly by encoding placeholders."""
    visible = [character for character in text if not character.isspace()]
    if not visible:
        return False
    placeholder_count = sum(character in {"?", "\ufffd"} for character in visible)
    return placeholder_count / len(visible) >= 0.4


def run_diagnosis(*, session_id: str, profile_id: str, trace_id: str, question: str, answer: str, timeout: float) -> dict[str, Any]:
    """Run the only formal scoring path: retrieval, one score call, validation and persistence."""
    if not question.strip() or not answer.strip():
        raise AppError("question and answer are required", code="invalid_diagnosis_input", status_code=422)
    if _looks_corrupted_text(question) or _looks_corrupted_text(answer):
        raise AppError(
            "question or answer appears corrupted; please submit the original text again",
            code="invalid_diagnosis_input",
            status_code=422,
        )
    deadline = time.monotonic() + max(1.0, timeout)
    knowledge = search_knowledge_safe(question=question, answer=answer, limit=5, trace_id=trace_id)
    add_trace_event(trace_id, "knowledge_retrieved", 1, {"count": len(knowledge)})
    add_trace_event(trace_id, "rrf_fused", 1, {"knowledge_ids": [item.get("id") for item in knowledge]})
    context = build_context(
        session_id=session_id,
        profile_id=profile_id,
        user_input=f"面试题：{question}\n回答：{answer}",
        knowledge_results=knowledge,
    )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AppError("diagnosis timeout", code="diagnosis_timeout", status_code=504)
    diagnosis = diagnose_interview(
        session_id=session_id,
        profile_id=profile_id,
        question=question,
        answer=answer,
        knowledge_context=knowledge,
        context_instruction=context,
        timeout=remaining,
    )
    content = diagnosis["content_scores"]
    voice = diagnosis["voice_scores"]
    overall = round(((content["total"] / content["max_total"]) * 5 + (voice["total"] / voice["max_total"]) * 5), 1)
    report_input = _report_structure(question, answer, diagnosis, overall, knowledge)
    report, output_check = HarnessRunner(session_id).post_output(report_input)
    if not output_check.get("valid"):
        add_trace_event(trace_id, "output_check", 3, {"result": "failed", "issues": output_check.get("issues", [])})
        raise AppError("Diagnosis report failed output validation", code="output_check_failed", status_code=503)
    saved = save_diagnosis_report(
        session_id=session_id, question=question, answer=answer, content_scores=content,
        voice_scores=voice, overall_score=overall, report_markdown=report, diagnosis=diagnosis,
    )
    add_trace_event(trace_id, "output_check", 3, {"result": "passed"})
    add_trace_event(trace_id, "diagnosis_evaluated", 2, {"report_id": saved["id"], "overall_score": overall})
    return {"report_id": saved["id"], "overall_score": overall, "report": report, "diagnosis": diagnosis,
            "followups": diagnosis["followups"], "memory_candidates": diagnosis["memory_candidates"],
            "sources": [item.get("source", item.get("title", "")) for item in knowledge]}


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
