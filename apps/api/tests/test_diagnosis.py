"""Tests for diagnosis business tools and memory."""

import pytest
from app.core.database import init_db, get_db
from app.diagnosis.diagnosis import (
    score_answer,
    analyze_voice_text,
    generate_followup,
    save_memory,
    get_memories,
    save_diagnosis_report,
    get_diagnosis_report,
)
from app.session.session import create_session


class TestScoreAnswer:
    """Tests for answer scoring."""

    def test_short_answer_low_score(self):
        result = score_answer("什么是 ReAct？", "ReAct 就是推理加行动")
        content_total = result["total"]
        assert content_total < 30  # Short answer should score low

    def test_off_topic_answer_low_alignment(self):
        result = score_answer(
            "什么是 Context Window 管理？",
            "ReAct 是一种让模型在思考和行动之间循环的模式，它可以让 Agent 更智能",
        )
        alignment = result["dimensions"]["question_alignment"]["score"]
        # Off-topic should have low alignment
        assert alignment < 7

    def test_good_answer_scores_higher(self):
        good_answer = """
首先，Context Window 管理是 Agent 工程中的核心问题。

首先是 Token 计数，不能靠字符数，要用 tiktoken 精确计算。

其次是 Sliding Window 策略，保留最近 N 轮对话。

第三是 Summarization，对旧消息生成摘要压缩。

最后是分层上下文策略，将 system prompt、memory、history 和当前 input 分预算管理。

在生产环境，我们遇到过搜索结果太长导致 context 爆炸的问题，解决方式是限制搜索结果长度和数量。

总之，Context Window 管理的核心是在有限窗口内保持对话连贯性，需要多种策略配合。
        """
        result = score_answer("什么是 Context Window 管理？", good_answer)
        assert result["total"] > 25  # Good answer should score higher
        assert result["total"] <= 50

    def test_all_dimensions_present(self):
        result = score_answer("问题", "一个中等长度的回答" * 10)
        dims = result["dimensions"]
        expected = ["concept_accuracy", "structure_completeness", "engineering_depth",
                    "example_quality", "question_alignment"]
        for dim in expected:
            assert dim in dims
            assert "score" in dims[dim]
            assert "explanation" in dims[dim]


class TestAnalyzeVoiceText:
    """Tests for voice analysis from transcripts."""

    def test_all_voice_dimensions_present(self):
        result = analyze_voice_text("一个正常的回答文本" * 20)
        dims = result["dimensions"]
        expected = ["fluency", "filler_words", "redundancy", "spoken_clarity", "answer_pacing"]
        for dim in expected:
            assert dim in dims
            assert "score" in dims[dim]

    def test_filler_words_detected(self):
        result = analyze_voice_text("嗯，就是那个，就是说，ReAct 的话，对吧，就是一种，嗯，循环模式")
        filler_score = result["dimensions"]["filler_words"]["score"]
        assert filler_score < 7  # Lots of fillers should lower score

    def test_redundant_text_lower_score(self):
        result = analyze_voice_text(
            "ReAct 是一种很好的模式，ReAct 模式很好用，ReAct 模式是非常好的一种模式。" * 10
        )
        redundancy_score = result["dimensions"]["redundancy"]["score"]
        # Redundant text should have lower score
        assert redundancy_score <= 8


class TestGenerateFollowup:
    """Tests for follow-up question generation."""

    def test_generates_followups(self):
        followups = generate_followup(
            "什么是 ReAct？",
            "ReAct 是推理加行动",
            weaknesses=["engineering_depth:2", "example_quality:2"],
        )
        assert len(followups) > 0
        for f in followups:
            assert "question" in f
            assert "why" in f

    def test_followup_limit(self):
        followups = generate_followup("Q", "A", weaknesses=["concept_accuracy:3"] * 10)
        assert len(followups) <= 5

    def test_fallback_followups(self):
        followups = generate_followup("Q", "A", weaknesses=[])
        assert len(followups) > 0


class TestMemory:
    """Tests for memory storage and retrieval."""

    def test_save_memory(self):
        init_db()
        session = create_session()
        entry = save_memory(session["id"], "weakness", "poor structure", "diagnosis")
        assert entry["key"] == "weakness"
        assert entry["value"] == "poor structure"
        assert entry["category"] == "diagnosis"

    def test_get_memories_by_session(self):
        init_db()
        session = create_session()
        save_memory(session["id"], "weakness", "poor structure", "diagnosis")
        save_memory(session["id"], "strength", "good examples", "diagnosis")

        memories = get_memories(session_id=session["id"])
        assert len(memories) == 2

    def test_get_memories_by_key(self):
        init_db()
        session = create_session()
        save_memory(session["id"], "weakness", "short answers", "diagnosis")

        memories = get_memories(key="weakness")
        assert len(memories) >= 1

    def test_first_diagnosis_saves_weakness(self):
        """Simulate: first diagnosis saves weakness, second reads it."""
        init_db()
        session1 = create_session()
        save_memory(session1["id"], "weakness", "engineering_depth_low", "diagnosis")

        # Second diagnosis
        session2 = create_session()
        # Read memories from previous sessions
        all_weaknesses = get_memories(key="weakness")
        assert len(all_weaknesses) >= 1
        assert any(m["value"] == "engineering_depth_low" for m in all_weaknesses)


class TestDiagnosisReport:
    """Tests for diagnosis report storage."""

    def test_save_and_get_report(self):
        init_db()
        session = create_session()
        report = save_diagnosis_report(
            session["id"],
            "什么是 ReAct？",
            "ReAct 就是推理加行动",
            {"concept_accuracy": {"score": 5, "explanation": "test"}},
            {"fluency": {"score": 7, "explanation": "test"}},
            60.0,
            "# Report\n\nTest report markdown",
        )
        assert "id" in report

        retrieved = get_diagnosis_report(report["id"])
        assert retrieved is not None
        assert retrieved["overall_score"] == 60.0
        assert retrieved["question"] == "什么是 ReAct？"
        assert "Report" in retrieved["report_markdown"]
