"""Tests for report rules, validation, and deterministic rendering."""

from offerpilot.diagnosis.reporting import load_diagnosis_rules, render_and_validate, rules_directory, validate_report


def _report() -> dict:
    dimensions = {name: {"score": 7, "explanation": "说明"} for name in ("concept_accuracy", "structure_completeness", "engineering_depth", "example_quality", "question_alignment")}
    voice = {name: {"score": 7, "explanation": "说明"} for name in ("fluency", "filler_words", "redundancy", "spoken_clarity", "answer_pacing")}
    return {"question": "什么是 ReAct？", "answer": "推理和行动循环", "overall_score": 7.0, "content_scores": {"dimensions": dimensions}, "voice_scores": {"dimensions": voice}, "followups": [], "sources": ["knowledge/test.md"], "exam_points": [{"point_id": "test-react-loop", "status": "covered", "evidence": "推理和行动循环", "explanation": "覆盖核心"}], "user_covered": [], "user_missing": [], "improvements": ["补充失败边界"], "reference_alignment": "- ReAct"}


def test_rules_are_read_from_configured_resources_directory():
    assert rules_directory().is_dir()
    assert load_diagnosis_rules()


def test_structured_report_is_valid_and_renders_required_sections():
    markdown, result = render_and_validate(_report())
    assert result["valid"]
    assert "## 内容维度评分" in markdown and "## 知识来源" in markdown


def test_invalid_report_is_rejected_without_legacy_text_checker():
    result = validate_report({"question": "incomplete"})
    assert not result["valid"]
