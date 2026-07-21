"""Structured single-call interview diagnosis and report persistence."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from app.core.database import get_db
from app.llm.llm_client import structured_json_completion


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
ALLOWED_MEMORY_KEYS = {
    "target_role",
    "weakness",
    "strength",
    "preference",
    "diagnosis_summary",
}
DEFAULT_LOCAL_PROFILE_ID = "00000000-0000-4000-8000-000000000001"


class ExamPointAssessment(BaseModel):
    point: str = Field(min_length=1, max_length=300)
    status: Literal["covered", "partial", "missing"]
    evidence: str | None = Field(default=None, max_length=500)
    explanation: str = Field(min_length=1, max_length=600)


def _truncate_text(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


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


def summarize_knowledge_context(knowledge_context: list[dict] | None) -> list[dict]:
    """Provide bounded reference evidence, not an open-domain knowledge prompt."""
    summaries = []
    for item in (knowledge_context or [])[:5]:
        summaries.append({
            "knowledge_id": item.get("id"),
            "title": item.get("title", ""),
            "source": item.get("source", item.get("source_file", "")),
            "question": item.get("question", ""),
            "expert_answer": _truncate_text(str(item.get("expert_answer", item.get("content", ""))), 900),
            "exam_points": list(item.get("exam_points") or [])[:6],
            "common_gaps": list(item.get("common_gaps") or [])[:5],
            "rrf_score": item.get("rrf_score"),
        })
    return summaries


def build_diagnosis_system_prompt(context_instruction: str) -> str:
    """Return the only system instruction used by the diagnosis model."""
    return f"""你是 OfferPilot Lite 的中文技术面试单题诊断器。
你不是知识库问答助手。你只处理“面试题 + 候选人回答”的诊断。
Reference Answers 只用于对标候选人回答，不用于回答知识问题。
只能根据输入中的题目、候选人原回答、参考证据和表达特征判断；不得补全候选人没有说过的能力或经历。
对每个参考考点输出 covered、partial 或 missing。covered 和 partial 必须引用候选人回答中的连续原文作为 evidence；missing 的 evidence 必须为 null。
语音维度只评估转写文本表达和程序特征，不评估音色、发音或真实语速。
所有文字使用简体中文。只输出 JSON 对象，字段必须为 exam_points、content_dimensions、voice_dimensions、improvements、followups、memory_candidates。

{context_instruction}"""


def _normalize_dimensions(raw: object, expected_keys: list[str]) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("dimensions must be an object")
    normalized = {}
    for key in expected_keys:
        value = raw.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"missing dimension: {key}")
        score = value.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError(f"invalid score for {key}")
        explanation = str(value.get("explanation", value.get("comment", ""))).strip()
        if not explanation:
            raise ValueError(f"missing explanation for {key}")
        normalized[key] = {"score": max(1, min(10, int(round(score)))), "explanation": explanation[:600]}
    return {"dimensions": normalized, "total": sum(v["score"] for v in normalized.values()), "max_total": len(expected_keys) * 10}


def _normalize_for_evidence(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "", flags=re.UNICODE).lower()


def _normalize_exam_points(raw: object, answer: str) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("exam_points must be a list")
    answer_normalized = _normalize_for_evidence(answer)
    points: list[dict] = []
    for item in raw[:20]:
        if "explanation" not in item and "diagnosis" in item:
            item = {**item, "explanation": item["diagnosis"]}
        assessment = ExamPointAssessment.model_validate(item)
        evidence = (assessment.evidence or "").strip()
        if assessment.status in {"covered", "partial"}:
            if not evidence or _normalize_for_evidence(evidence) not in answer_normalized:
                raise ValueError("covered/partial evidence must be quoted from the candidate answer")
        elif evidence:
            raise ValueError("missing exam point evidence must be null")
        points.append({
            "point": assessment.point,
            "status": assessment.status,
            "evidence": evidence or None,
            "explanation": assessment.explanation,
        })
    return points


def _normalize_followups(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("followups must be a list")
    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        why = str(item.get("why", "")).strip()
        if question and why and question not in seen:
            cleaned.append({"question": question[:300], "why": why[:300]})
            seen.add(question)
        if len(cleaned) == 5:
            break
    return cleaned


def normalize_memory_candidates(session_id: str, profile_id: str, raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("memory_candidates must be a list")
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        value = str(item.get("value", "")).strip()
        category = str(item.get("category", "general")).strip() or "general"
        if key not in ALLOWED_MEMORY_KEYS or not value or len(value) > 300:
            continue
        dedupe_key = (key, value)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append({"session_id": session_id, "profile_id": profile_id, "key": key, "value": value, "category": category, "source": "llm"})
        if len(candidates) == 6:
            break
    return candidates


def normalize_diagnosis_result(
    raw: dict,
    answer: str,
    session_id: str,
    profile_id: str = DEFAULT_LOCAL_PROFILE_ID,
) -> dict:
    """Validate the complete model contract before report rendering."""
    if not isinstance(raw, dict):
        raise ValueError("diagnosis result must be an object")
    return {
        "exam_points": _normalize_exam_points(raw.get("exam_points"), answer),
        "content_scores": _normalize_dimensions(raw.get("content_dimensions"), CONTENT_DIMENSION_KEYS),
        "voice_scores": _normalize_dimensions(raw.get("voice_dimensions"), VOICE_DIMENSION_KEYS),
        "improvements": [str(item).strip()[:400] for item in raw.get("improvements", []) if str(item).strip()][:8],
        "followups": _normalize_followups(raw.get("followups")),
        "memory_candidates": normalize_memory_candidates(session_id, profile_id, raw.get("memory_candidates")),
    }


def diagnose_interview(
    *,
    session_id: str,
    profile_id: str = DEFAULT_LOCAL_PROFILE_ID,
    question: str,
    answer: str,
    knowledge_context: list[dict],
    context_instruction: str,
    timeout: float,
) -> dict:
    """Make exactly one LLM call for all semantic diagnosis decisions."""
    payload = {
        "question": question,
        "candidate_answer": _truncate_text(answer, 7000),
        "reference_evidence": summarize_knowledge_context(knowledge_context),
        "voice_features": extract_voice_features(answer),
    }
    result = structured_json_completion(
        task_name="interview_diagnosis",
        system_prompt=build_diagnosis_system_prompt(context_instruction),
        user_payload=payload,
        validator=lambda data: _can_normalize_diagnosis(data, answer, session_id, profile_id),
        temperature=0.1,
        timeout=max(1.0, timeout),
    )
    normalized = normalize_diagnosis_result(result.data, answer, session_id, profile_id)
    normalized["source"] = result.source
    normalized["voice_features"] = payload["voice_features"]
    return normalized


def _can_normalize_diagnosis(raw: dict, answer: str, session_id: str, profile_id: str) -> bool:
    try:
        normalize_diagnosis_result(raw, answer, session_id, profile_id)
        return True
    except Exception:
        return False


def get_memories(profile_id: str, key: str | None = None) -> list[dict]:
    conn = get_db()
    try:
        conditions, params = [], []
        conditions.append("profile_id = ?")
        params.append(profile_id)
        if key:
            conditions.append("key = ?")
            params.append(key)
        rows = conn.execute(
            f"SELECT id, session_id, profile_id, key, value, category, created_at FROM memories WHERE {' AND '.join(conditions)} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def save_memory(
    session_id: str,
    key: str,
    value: str,
    category: str = "general",
    profile_id: str = DEFAULT_LOCAL_PROFILE_ID,
) -> dict:
    if key not in ALLOWED_MEMORY_KEYS or not value.strip():
        raise ValueError("invalid memory candidate")
    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "INSERT INTO memories (session_id, profile_id, key, value, category, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, profile_id, key, value[:300], category, now),
        )
        conn.commit()
        return {"id": cursor.lastrowid, "session_id": session_id, "profile_id": profile_id, "key": key, "value": value[:300], "category": category, "created_at": now}
    finally:
        conn.close()


def save_diagnosis_report(
    *,
    session_id: str,
    question: str,
    answer: str,
    content_scores: dict,
    voice_scores: dict,
    overall_score: float,
    report_markdown: str,
    diagnosis: dict,
) -> dict:
    conn = get_db()
    try:
        report_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO diagnosis_reports (id, session_id, question, answer, content_scores, voice_scores, overall_score, report_markdown, diagnosis_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (report_id, session_id, question, answer, json.dumps(content_scores, ensure_ascii=False), json.dumps(voice_scores, ensure_ascii=False), overall_score, report_markdown, json.dumps(diagnosis, ensure_ascii=False), now),
        )
        conn.commit()
        return {"id": report_id, "session_id": session_id, "overall_score": overall_score, "created_at": now}
    finally:
        conn.close()


def get_diagnosis_report(report_id: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM diagnosis_reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            return None
        diagnosis_json = row["diagnosis_json"] if "diagnosis_json" in row.keys() else "{}"
        return {
            "id": row["id"], "session_id": row["session_id"], "question": row["question"], "answer": row["answer"],
            "content_scores": json.loads(row["content_scores"]), "voice_scores": json.loads(row["voice_scores"]),
            "overall_score": row["overall_score"], "report_markdown": row["report_markdown"],
            "diagnosis": json.loads(diagnosis_json or "{}"), "created_at": row["created_at"],
        }
    finally:
        conn.close()


def list_recent_reports(profile_id: str, limit: int = 5) -> list[dict]:
    """Return bounded report summaries owned by one profile."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT r.id, r.question, r.overall_score, r.diagnosis_json, r.created_at "
            "FROM diagnosis_reports r JOIN sessions s ON s.id = r.session_id "
            "WHERE s.profile_id = ? ORDER BY r.created_at DESC LIMIT ?",
            (profile_id, max(1, min(limit, 10))),
        ).fetchall()
        result = []
        for row in rows:
            diagnosis = json.loads(row["diagnosis_json"] or "{}")
            gaps = [item.get("point", "") for item in diagnosis.get("exam_points", []) if item.get("status") != "covered"]
            result.append({"id": row["id"], "question": row["question"], "overall_score": row["overall_score"], "missing_exam_points": gaps[:5], "created_at": row["created_at"]})
        return result
    finally:
        conn.close()
