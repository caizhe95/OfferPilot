"""Structured single-call interview diagnosis and report persistence."""

from __future__ import annotations

import inspect
import re
import asyncio
from time import perf_counter
from typing import Annotated, Any, Literal

from pydantic import BaseModel, StringConstraints
from offerpilot.diagnosis.models import (
    ContentDimensions,
    DiagnosisModelOutput,
    DimensionAssessment,
    ExamPointAssessment,
    VoiceDimensions,
)

from offerpilot.llm.structured import LLMInvalidResponseError, structured_json_completion, structured_result_metrics


CONTENT_DIMENSION_KEYS = [
    "concept_accuracy",
    "structure_completeness",
    "engineering_depth",
    "example_quality",
    "question_alignment",
]
VOICE_DIMENSION_KEYS = [
    "fluency",
    "filler_words",
    "redundancy",
    "spoken_clarity",
    "answer_pacing",
]
EvidenceText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
PointExplanation = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
DimensionExplanation = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
ImprovementText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


def extract_voice_features(transcript: str) -> dict:
    """Extract objective features; no LLM judgement occurs here."""
    text = transcript or ""
    text_lower = text.lower()
    fillers_cn = ["嗯", "呃", "啊", "就是", "就是说", "那个", "这个", "对吧", "对不对", "反正", "基本上"]
    fillers_en = ["um", "uh", "like", "you know", "i mean", "actually", "basically"]
    filler_count = sum(text_lower.count(term) for term in fillers_cn + fillers_en)
    sentence_count = max(1, len([s for s in re.split(r"[。！？!?]", text) if s.strip()]))
    paragraph_count = max(1, len([p for p in text.split("\n\n") if p.strip()]))
    return {
        "char_count": len(text),
        "sentence_count": sentence_count,
        "paragraph_count": paragraph_count,
        "filler_count": filler_count,
        "filler_per_sentence": round(filler_count / sentence_count, 2),
    }


def build_diagnosis_system_prompt(context_instruction: str) -> str:
    """Return the only system instruction used by the diagnosis model."""
    return f"""你是 OfferPilot Lite 的中文技术面试单题诊断器。
你不是知识库问答助手。你只处理“面试题 + 候选人回答”的诊断。
Reference Answers 只用于对标候选人回答，不用于回答知识问题。
只能根据输入中的题目、候选人原回答、参考证据和表达特征判断；不得补全候选人没有说过的能力或经历。
对每个 scorable_exam_points 中的考点恰好输出一次 covered、partial 或 missing，并使用该考点原样的 point_id。covered 和 partial 必须引用候选人回答中的连续原文作为 evidence；missing 的 evidence 必须为 null。
语音维度只评估转写文本表达和程序特征，不评估音色、发音或真实语速。
所有文字使用简体中文。只输出紧凑 JSON，不得增加字段。完整结构为：
{{"exam_points":[{{"point_id":"稳定考点ID","status":"covered|partial|missing","evidence":"候选人连续原文或null","explanation":"不超过120字符"}}],"content_dimensions":{{"concept_accuracy":{{"score":1-10,"explanation":"不超过80字符"}},"structure_completeness":{{"score":1-10,"explanation":"不超过80字符"}},"engineering_depth":{{"score":1-10,"explanation":"不超过80字符"}},"example_quality":{{"score":1-10,"explanation":"不超过80字符"}},"question_alignment":{{"score":1-10,"explanation":"不超过80字符"}}}},"voice_dimensions":{{"fluency":{{"score":1-10,"explanation":"不超过80字符"}},"filler_words":{{"score":1-10,"explanation":"不超过80字符"}},"redundancy":{{"score":1-10,"explanation":"不超过80字符"}},"spoken_clarity":{{"score":1-10,"explanation":"不超过80字符"}},"answer_pacing":{{"score":1-10,"explanation":"不超过80字符"}}}},"improvements":["最多3条，每条不超过160字符"]}}
evidence 最多 240 字符；不得输出考点名称、总分、报告、followups 或 memory_candidates。

{context_instruction}"""


def _normalize_dimensions(raw: BaseModel, expected_keys: list[str]) -> dict:
    values = raw.model_dump()
    normalized: dict[str, dict[str, Any]] = {}
    for key in expected_keys:
        value = values[key]
        normalized[key] = {"score": int(round(value["score"])), "explanation": value["explanation"]}
    return {"dimensions": normalized, "total": sum(v["score"] for v in normalized.values()), "max_total": len(expected_keys) * 10}


def select_scorable_points(knowledge_context: list[dict], limit: int = 12) -> list[dict]:
    selected: list[dict] = []
    seen: set[str] = set()
    for item in knowledge_context[:5]:
        for point in item.get("exam_point_refs") or []:
            point_id = str(point.get("id", ""))
            label = str(point.get("label", "")).strip()
            knowledge_id = point.get("knowledge_id")
            if not point_id or not label or point_id in seen:
                continue
            seen.add(point_id)
            selected.append({"id": point_id, "label": label, "knowledge_id": knowledge_id})
            if len(selected) == limit:
                return selected
    return selected


def _normalize_exam_points(raw: list[ExamPointAssessment], answer: str, scorable_points: list[dict]) -> list[dict]:
    allowed = {str(point["id"]): point for point in scorable_points}
    if len(raw) != len(allowed):
        raise ValueError("exam_points must cover every selected stable point exactly once")
    assessments: dict[str, ExamPointAssessment] = {}
    for assessment in raw:
        if assessment.point_id not in allowed or assessment.point_id in assessments:
            raise ValueError("exam point contains an illegal or duplicate point_id")
        assessments[assessment.point_id] = assessment

    points: list[dict] = []
    for selected in scorable_points:
        assessment = assessments[str(selected["id"])]
        evidence = assessment.evidence
        if assessment.status in {"covered", "partial"}:
            if evidence is None or evidence not in answer:
                raise ValueError("covered/partial evidence must be quoted from the candidate answer")
        elif evidence is not None:
            raise ValueError("missing exam point evidence must be null")
        points.append({
            "point_id": assessment.point_id,
            "point": str(allowed[assessment.point_id]["label"]),
            "status": assessment.status,
            "evidence": evidence,
            "explanation": assessment.explanation,
            "knowledge_id": allowed[assessment.point_id].get("knowledge_id"),
        })
    return points


def build_deterministic_followups(diagnosis: dict[str, Any], knowledge_context: list[dict], limit: int = 5) -> list[dict]:
    presets: dict[str, list[str]] = {}
    for item in knowledge_context:
        knowledge_id = str(item.get("id", ""))
        if not knowledge_id:
            continue
        presets[knowledge_id] = [str(question).strip() for question in item.get("followups", []) if str(question).strip()]

    result: list[dict] = []
    used_questions: set[str] = set()
    for point in diagnosis.get("exam_points", []):
        if point.get("status") not in {"partial", "missing"}:
            continue
        question = ""
        for candidate in presets.get(str(point.get("knowledge_id", "")), []):
            if candidate not in used_questions:
                question = candidate
                break
        label = str(point.get("point", point.get("point_id", "该考点"))).strip()[:120]
        if not question:
            question = f"请补充说明“{label}”的核心原理、工程实现与边界条件。"
        if question in used_questions:
            question = f"请针对考点 {point['point_id']} 补充核心原理、工程实现与边界条件。"
        question = question[:300]
        used_questions.add(question)
        result.append({
            "question": question,
            "why": str(point.get("explanation", ""))[:120],
            "exam_point_id": str(point["point_id"]),
        })
        if len(result) == max(0, min(limit, 5)):
            break
    return result


def normalize_diagnosis_result(
    raw: dict,
    answer: str,
    scorable_points: list[dict] | None = None,
) -> dict:
    """Validate the complete model contract before report rendering."""
    if not isinstance(raw, dict):
        raise ValueError("diagnosis result must be an object")
    selected = list(scorable_points or [])
    if not selected:
        raise ValueError("diagnosis requires stable exam points from retrieval")
    model_output = DiagnosisModelOutput.model_validate(raw)
    points = _normalize_exam_points(model_output.exam_points, answer, selected)
    return {
        "exam_points": points,
        "content_scores": _normalize_dimensions(model_output.content_dimensions, CONTENT_DIMENSION_KEYS),
        "voice_scores": _normalize_dimensions(model_output.voice_dimensions, VOICE_DIMENSION_KEYS),
        "improvements": list(model_output.improvements),
    }


async def diagnose_interview(
    *,
    question: str,
    answer: str,
    scorable_points: list[dict],
    context_instruction: str,
    timeout: float,
    cancel_event: asyncio.Event | None = None,
    deadline: float | None = None,
    on_progress=None,
    on_fallback=None,
    run_id: str | None = None,
    session_id: str | None = None,
    profile_id: str | None = None,
) -> dict:
    """Make exactly one LLM call for all semantic diagnosis decisions."""
    payload = {
        "question": question,
        "candidate_answer": answer,
        "scorable_exam_points": scorable_points,
        "voice_features": extract_voice_features(answer),
    }
    result: Any = structured_json_completion(
        task_name="interview_diagnosis",
        system_prompt=build_diagnosis_system_prompt(context_instruction),
        user_payload=payload,
        temperature=0.1,
        timeout=max(0.001, timeout),
        cancel_event=cancel_event,
        deadline=deadline,
        on_progress=on_progress,
        on_fallback=on_fallback,
        run_id=run_id,
        session_id=session_id,
        profile_id=profile_id,
        logical_call_id="diagnosis:structured",
    )
    if inspect.isawaitable(result):
        result = await result
    metrics = structured_result_metrics(result)
    adapter_validation_ms = int(getattr(result, "validation_duration_ms", 0) or 0)
    validation_started = perf_counter()
    try:
        normalized = normalize_diagnosis_result(result.data, answer, scorable_points)
    except Exception as exc:
        validation_duration_ms = adapter_validation_ms + max(0, int(round((perf_counter() - validation_started) * 1000)))
        raise LLMInvalidResponseError(
            "interview_diagnosis: invalid structured response",
            metrics=metrics,
            validation_duration_ms=validation_duration_ms,
        ) from exc
    normalized["_llm_metrics"] = metrics
    normalized["_validation_duration_ms"] = adapter_validation_ms + max(0, int(round((perf_counter() - validation_started) * 1000)))
    normalized["source"] = result.source
    normalized["voice_features"] = payload["voice_features"]
    return normalized
