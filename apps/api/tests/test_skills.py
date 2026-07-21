"""Tests for skills loader, validator, and intent matcher."""

from pathlib import Path
import pytest
from app.skills.skills_loader import (
    validate_skill,
    validate_all_skills,
    load_skill,
    load_skill_references,
    list_skills,
    match_skill,
    _find_skills_dir,
)


@pytest.fixture
def skills_dir():
    return _find_skills_dir()


class TestSkillValidation:
    """Tests for skill validation."""

    def test_all_skills_valid(self, skills_dir):
        """All built-in skills should pass validation."""
        results = validate_all_skills(skills_dir)
        for name, issues in results.items():
            assert len(issues) == 0, f"Skill '{name}' has issues: {issues}"

    def test_interview_diagnosis_valid(self, skills_dir):
        issues = validate_skill(skills_dir / "interview-diagnosis")
        assert len(issues) == 0

    def test_answer_rewrite_valid(self, skills_dir):
        issues = validate_skill(skills_dir / "answer-rewrite")
        assert len(issues) == 0

    def test_followup_coaching_valid(self, skills_dir):
        issues = validate_skill(skills_dir / "followup-coaching")
        assert len(issues) == 0

    def test_audio_diagnosis_valid(self, skills_dir):
        issues = validate_skill(skills_dir / "audio-diagnosis")
        assert len(issues) == 0


class TestSkillLoading:
    """Tests for skill content loading."""

    def test_load_interview_diagnosis(self, skills_dir):
        skill = load_skill("interview-diagnosis", skills_dir)
        assert skill is not None
        assert skill["name"] == "interview-diagnosis"
        assert len(skill["description"]) > 20
        assert len(skill["body"]) > 50

    def test_load_answer_rewrite(self, skills_dir):
        skill = load_skill("answer-rewrite", skills_dir)
        assert skill is not None
        assert skill["name"] == "answer-rewrite"

    def test_load_followup_coaching(self, skills_dir):
        skill = load_skill("followup-coaching", skills_dir)
        assert skill is not None
        assert skill["name"] == "followup-coaching"

    def test_load_audio_diagnosis(self, skills_dir):
        skill = load_skill("audio-diagnosis", skills_dir)
        assert skill is not None
        assert skill["name"] == "audio-diagnosis"

    def test_load_nonexistent_skill(self):
        assert load_skill("nonexistent") is None

    def test_load_references(self, skills_dir):
        refs = load_skill_references("interview-diagnosis", skills_dir)
        assert len(refs) >= 2
        ref_names = [r["name"] for r in refs]
        assert "rubric.md" in ref_names
        assert "output-contract.md" in ref_names
        # Each ref should have content
        for ref in refs:
            assert len(ref["content"]) > 50

    def test_list_skills(self, skills_dir):
        skills = list_skills(skills_dir)
        assert len(skills) == 4
        names = [s["name"] for s in skills]
        assert "interview-diagnosis" in names
        assert "answer-rewrite" in names
        assert "followup-coaching" in names
        assert "audio-diagnosis" in names


class TestIntentMatching:
    """Tests for skill intent matching."""

    def test_match_interview_diagnosis_by_question(self):
        skill = match_skill("什么是 ReAct？我这样回答对吗？")
        assert skill is not None
        assert skill["name"] == "interview-diagnosis"

    def test_match_interview_diagnosis_by_diagnose(self):
        skill = match_skill("请帮我诊断这个面试回答")
        assert skill is not None
        assert skill["name"] == "interview-diagnosis"

    def test_match_answer_rewrite_by_optimize(self):
        skill = match_skill("帮我优化一下这个回答")
        assert skill is not None
        assert skill["name"] == "answer-rewrite"

    def test_match_answer_rewrite_by_rewrite(self):
        skill = match_skill("改写这个问题")
        assert skill is not None
        assert skill["name"] == "answer-rewrite"

    def test_match_followup_coaching(self):
        skill = match_skill("面试官可能会追问什么？")
        assert skill is not None
        assert skill["name"] == "followup-coaching"

    def test_match_followup_coaching_by_coaching(self):
        skill = match_skill("给我一些追问的回答策略")
        assert skill is not None
        assert skill["name"] == "followup-coaching"

    def test_match_audio_diagnosis_by_audio(self):
        skill = match_skill("我上传了面试录音音频，帮我分析")
        assert skill is not None
        assert skill["name"] == "audio-diagnosis"

    def test_match_audio_diagnosis_by_transcript(self):
        skill = match_skill("这是面试的转写文本")
        assert skill is not None
        assert skill["name"] == "audio-diagnosis"

    def test_match_returns_none_for_unknown(self):
        skill = match_skill("今天天气怎么样")
        assert skill is None

    def test_untriggered_skill_not_loaded_for_wrong_input(self):
        # answer-rewrite should NOT match a diagnosis request
        skill = match_skill("诊断我的回答：ReAct 是什么")
        assert skill is not None
        assert skill["name"] == "interview-diagnosis"
