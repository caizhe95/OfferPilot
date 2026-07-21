"""Tests for the unified evidence-grounded diagnosis contract."""

import pytest
from types import SimpleNamespace

from app.core.errors import AppError
from app.diagnosis.diagnosis import (
    diagnose_interview,
    normalize_diagnosis_result,
)
from app.diagnosis.workflow import _looks_corrupted_text, run_diagnosis


def _result(answer: str) -> dict:
    return {
        "exam_points": [{
            "point": "说明 ReAct 的推理与行动循环",
            "status": "covered",
            "evidence": answer,
            "explanation": "回答明确描述了循环。",
        }],
        "content_dimensions": {
            key: {"score": 7, "explanation": "内容可用"}
            for key in ["concept_accuracy", "structure_completeness", "engineering_depth", "example_quality", "question_alignment"]
        },
        "voice_dimensions": {
            key: {"score": 7, "explanation": "表达清楚"}
            for key in ["fluency", "filler_words", "redundancy", "spoken_clarity", "answer_pacing"]
        },
        "improvements": ["补充工具失败处理。"],
        "followups": [{"question": "工具调用失败怎么办？", "why": "验证容错能力"}],
        "memory_candidates": [{"key": "weakness", "value": "补充容错细节", "category": "diagnosis"}],
    }


def test_diagnose_interview_returns_all_sections():
    answer = "ReAct 让模型在推理和行动之间循环。"
    result = diagnose_interview(
        session_id="test", question="什么是 ReAct？", answer=answer,
        knowledge_context=[], context_instruction="测试规则", timeout=10,
    )
    assert set(result["content_scores"]["dimensions"]) == {
        "concept_accuracy", "structure_completeness", "engineering_depth", "example_quality", "question_alignment",
    }
    assert result["exam_points"][0]["evidence"] in answer


def test_covered_evidence_must_be_in_answer():
    raw = _result("ReAct 是推理和行动循环。")
    raw["exam_points"][0]["evidence"] = "模型没有说过的内容"
    with pytest.raises(ValueError, match="evidence"):
        normalize_diagnosis_result(raw, "ReAct 是推理和行动循环。", "test")


def test_missing_exam_point_cannot_have_evidence():
    raw = _result("ReAct 是推理和行动循环。")
    raw["exam_points"][0].update({"status": "missing", "evidence": "ReAct"})
    with pytest.raises(ValueError, match="missing"):
        normalize_diagnosis_result(raw, "ReAct 是推理和行动循环。", "test")


def test_context_boundaries_are_sent_to_the_single_llm_call(monkeypatch):
    import app.diagnosis.diagnosis as diagnosis_module

    captured = {}
    answer = "ReAct 是推理和行动循环。"

    call_count = 0

    def fake_completion(**kwargs):
        nonlocal call_count
        call_count += 1
        captured.update(kwargs)
        return SimpleNamespace(data=_result(answer), source="llm")

    monkeypatch.setattr(diagnosis_module, "structured_json_completion", fake_completion)
    diagnose_interview(
        session_id="test", question="什么是 ReAct？", answer=answer,
        knowledge_context=[], context_instruction="上下文规则", timeout=10,
    )
    assert "不是知识库问答助手" in captured["system_prompt"]
    assert "面试题 + 候选人回答" in captured["system_prompt"]
    assert "上下文规则" in captured["system_prompt"]
    assert call_count == 1


def test_legacy_scoring_endpoints_are_not_registered(client):
    assert client.post("/api/tools/score-answer", json={}).status_code == 404
    assert client.post("/api/tools/analyze-voice-text", json={}).status_code == 404
    assert client.post("/api/tools/generate-followup", json={}).status_code == 404


def test_diagnosis_rejects_encoding_placeholder_text():
    assert _looks_corrupted_text("??? RAG ??????")
    assert not _looks_corrupted_text("如何用 RAG 处理检索失败？")
    with pytest.raises(AppError, match="appears corrupted"):
        run_diagnosis(
            session_id="test",
            profile_id="profile",
            trace_id="trace",
            question="??? RAG ??????",
            answer="???? FTS5 ?????? Embedding",
            timeout=10,
        )
