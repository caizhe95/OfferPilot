"""Tests for the unified evidence-grounded diagnosis contract."""

import pytest
from types import SimpleNamespace

from offerpilot.core.errors import AppError
from offerpilot.diagnosis.scoring import (
    build_deterministic_followups,
    diagnose_interview,
    normalize_diagnosis_result,
)
from offerpilot.diagnosis.workflow import calculate_overall_score, looks_corrupted_text, run_diagnosis

SCORABLE_POINTS = [{"id": "test-react-loop", "label": "说明 ReAct 的推理与行动循环", "knowledge_id": 1}]


def _scorable_points(count: int) -> list[dict]:
    return [
        {"id": f"test-point-{index}", "label": f"考点 {index}", "knowledge_id": index // 2 + 1}
        for index in range(count)
    ]


def _result(answer: str) -> dict:
    return {
        "exam_points": [{
            "point_id": "test-react-loop",
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
    }


def _result_for_points(answer: str, points: list[dict], status: str = "covered") -> dict:
    result = _result(answer)
    result["exam_points"] = [
        {
            "point_id": point["id"],
            "status": status,
            "evidence": answer if status != "missing" else None,
            "explanation": f"{point['label']} 的判断依据。",
        }
        for point in points
    ]
    return result


@pytest.mark.asyncio
async def test_diagnose_interview_returns_all_sections():
    answer = "ReAct 让模型在推理和行动之间循环。"
    result = await diagnose_interview(
        question="什么是 ReAct？", answer=answer,
        scorable_points=SCORABLE_POINTS, context_instruction="测试规则", timeout=10,
    )
    assert set(result["content_scores"]["dimensions"]) == {
        "concept_accuracy", "structure_completeness", "engineering_depth", "example_quality", "question_alignment",
    }
    assert result["exam_points"][0]["evidence"] in answer


def test_covered_evidence_must_be_in_answer():
    raw = _result("ReAct 是推理和行动循环。")
    raw["exam_points"][0]["evidence"] = "模型没有说过的内容"
    with pytest.raises(ValueError, match="evidence"):
        normalize_diagnosis_result(raw, "ReAct 是推理和行动循环。", scorable_points=SCORABLE_POINTS)


def test_missing_exam_point_cannot_have_evidence():
    raw = _result("ReAct 是推理和行动循环。")
    raw["exam_points"][0].update({"status": "missing", "evidence": "ReAct"})
    with pytest.raises(ValueError, match="missing"):
        normalize_diagnosis_result(raw, "ReAct 是推理和行动循环。", scorable_points=SCORABLE_POINTS)


def test_diagnosis_rejects_unknown_stable_point_id():
    raw = _result("ReAct 是推理和行动循环。")
    raw["exam_points"][0]["point_id"] = "unknown-point"
    with pytest.raises(ValueError, match="illegal"):
        normalize_diagnosis_result(raw, "ReAct 是推理和行动循环。", scorable_points=SCORABLE_POINTS)


def test_diagnosis_derives_exam_point_label_from_its_stable_id():
    raw = _result("ReAct 是推理和行动循环。")
    normalized = normalize_diagnosis_result(raw, "ReAct 是推理和行动循环。", scorable_points=SCORABLE_POINTS)
    assert normalized["exam_points"][0]["point"] == SCORABLE_POINTS[0]["label"]


@pytest.mark.parametrize("count", [1, 8, 12])
def test_diagnosis_validates_complete_stable_point_sets(count):
    answer = "候选人回答中的连续证据。"
    points = _scorable_points(count)
    normalized = normalize_diagnosis_result(_result_for_points(answer, points), answer, scorable_points=points)
    assert [item["point_id"] for item in normalized["exam_points"]] == [point["id"] for point in points]


def test_diagnosis_rejects_duplicate_and_missing_stable_points():
    answer = "候选人回答中的连续证据。"
    points = _scorable_points(2)
    duplicate = _result_for_points(answer, points)
    duplicate["exam_points"][1]["point_id"] = duplicate["exam_points"][0]["point_id"]
    with pytest.raises(ValueError, match="duplicate"):
        normalize_diagnosis_result(duplicate, answer, scorable_points=points)

    missing = _result_for_points(answer, points)
    missing["exam_points"].pop()
    with pytest.raises(ValueError, match="cover every"):
        normalize_diagnosis_result(missing, answer, scorable_points=points)


def test_diagnosis_rejects_extra_model_fields_and_non_contiguous_evidence():
    answer = "ReAct 让模型先推理，再行动。"
    raw = _result(answer)
    raw["followups"] = []
    with pytest.raises(ValueError, match="extra"):
        normalize_diagnosis_result(raw, answer, scorable_points=SCORABLE_POINTS)

    raw = _result(answer)
    raw["exam_points"][0]["point"] = "模型不应返回考点标签"
    with pytest.raises(ValueError, match="extra"):
        normalize_diagnosis_result(raw, answer, scorable_points=SCORABLE_POINTS)

    raw = _result(answer)
    raw["exam_points"][0]["evidence"] = "ReAct让模型先推理再行动"
    with pytest.raises(ValueError, match="quoted"):
        normalize_diagnosis_result(raw, answer, scorable_points=SCORABLE_POINTS)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw["exam_points"][0].update({"evidence": "证" * 241}),
        lambda raw: raw["exam_points"][0].update({"explanation": "解" * 121}),
        lambda raw: raw["content_dimensions"]["concept_accuracy"].update({"explanation": "维" * 81}),
        lambda raw: raw["content_dimensions"]["concept_accuracy"].update({"score": "7"}),
        lambda raw: raw.update({"improvements": ["建议"] * 4}),
        lambda raw: raw.update({"improvements": ["建" * 161]}),
    ],
)
def test_diagnosis_rejects_output_over_contract_limits(mutate):
    answer = "证" * 300
    raw = _result(answer)
    raw["exam_points"][0]["evidence"] = "证" * 240
    mutate(raw)
    with pytest.raises(ValueError):
        normalize_diagnosis_result(raw, answer, scorable_points=SCORABLE_POINTS)


def test_missing_evidence_must_be_json_null_not_an_empty_string():
    raw = _result("候选人回答")
    raw["exam_points"][0].update({"status": "missing", "evidence": ""})
    with pytest.raises(ValueError):
        normalize_diagnosis_result(raw, "候选人回答", scorable_points=SCORABLE_POINTS)


def test_overall_score_uses_both_dimensions():
    scores = {"total": 35, "max_total": 50}
    assert calculate_overall_score(scores, scores) == 7.0


@pytest.mark.parametrize("invalid", [0, -1, None, "50", True, float("nan"), float("inf")])
def test_overall_score_rejects_invalid_denominator(invalid):
    with pytest.raises(AppError, match="score contract is invalid"):
        calculate_overall_score({"total": 35, "max_total": invalid}, {"total": 35, "max_total": 50})


def test_followups_use_unused_knowledge_presets_then_fixed_template_deterministically():
    points = _scorable_points(7)
    diagnosis = normalize_diagnosis_result(
        _result_for_points("候选人回答", points, status="missing"),
        "候选人回答",
        scorable_points=points,
    )
    knowledge = [
        {"id": 1, "followups": ["预设追问一", "预设追问二"]},
        {"id": 2, "followups": []},
        {"id": 3, "followups": ["预设追问三"]},
        {"id": 4, "followups": []},
    ]
    first = build_deterministic_followups(diagnosis, knowledge)
    second = build_deterministic_followups(diagnosis, knowledge)
    assert first == second
    assert len(first) == 5
    assert [item["question"] for item in first[:2]] == ["预设追问一", "预设追问二"]
    assert first[2]["question"].startswith("请补充说明")
    assert first[0]["why"] == diagnosis["exam_points"][0]["explanation"]
    assert [item["exam_point_id"] for item in first] == [point["id"] for point in points[:5]]


@pytest.mark.asyncio
async def test_context_boundaries_are_sent_to_the_single_llm_call(monkeypatch):
    import offerpilot.diagnosis.scoring as diagnosis_module

    captured = {}
    answer = "ReAct 是推理和行动循环。"

    call_count = 0

    def fake_completion(**kwargs):
        nonlocal call_count
        call_count += 1
        captured.update(kwargs)
        return SimpleNamespace(data=_result(answer), source="llm")

    monkeypatch.setattr(diagnosis_module, "structured_json_completion", fake_completion)
    await diagnose_interview(
        question="什么是 ReAct？", answer=answer,
        scorable_points=SCORABLE_POINTS, context_instruction="上下文规则", timeout=10,
    )
    assert "不是知识库问答助手" in captured["system_prompt"]
    assert "面试题 + 候选人回答" in captured["system_prompt"]
    assert "上下文规则" in captured["system_prompt"]
    assert "followups 或 memory_candidates" in captured["system_prompt"]
    assert "reference_evidence" not in captured["user_payload"]
    assert call_count == 1


def test_legacy_scoring_endpoints_are_not_registered(client):
    assert client.post("/api/tools/score-answer", json={}).status_code == 404
    assert client.post("/api/tools/analyze-voice-text", json={}).status_code == 404
    assert client.post("/api/tools/generate-followup", json={}).status_code == 404


@pytest.mark.asyncio
async def test_eval_regression_suite_uses_the_compact_diagnosis_contract():
    from offerpilot.database.connection import init_db
    from offerpilot.evaluations.runner import run_all_evals

    init_db()
    result = await run_all_evals()
    assert result["failed"] == 0
    assert result["passed"] == result["total"] == 3


@pytest.mark.asyncio
async def test_diagnosis_rejects_encoding_placeholder_text():
    assert looks_corrupted_text("??? RAG ??????")
    assert not looks_corrupted_text("如何用 RAG 处理检索失败？")
    with pytest.raises(AppError, match="appears corrupted"):
        await run_diagnosis(
            session_id="test",
            profile_id="profile",
            run_id="run",
            question="??? RAG ??????",
            answer="???? FTS5 ?????? Embedding",
            timeout=10,
        )
