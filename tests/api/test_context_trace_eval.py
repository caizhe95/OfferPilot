"""Tests for context builder, trace, and eval systems."""

import pytest
from offerpilot.core.database import init_db, get_db
from offerpilot.session.session import create_session, add_message
from offerpilot.diagnosis.diagnosis import save_memory
from offerpilot.diagnosis.context_builder import build_context
from offerpilot.trace.trace_eval import (
    create_trace,
    add_trace_event,
    complete_trace,
    get_trace,
    run_eval,
    run_all_evals,
    save_eval_run,
    EVAL_CASES,
)


class TestContextBuilder:
    """Tests for context assembly."""

    def test_build_context_basic(self):
        init_db()
        session = create_session()
        add_message(session["id"], "user", "什么是 ReAct？")

        context = build_context(
            session["id"],
            session["profile_id"],
            "诊断我的回答",
        )
        assert "system" in context.lower() or "SYSTEM" in context
        assert "interview evaluator" in context.lower()
        assert "诊断我的回答" in context

    def test_build_context_includes_diagnosis_boundary(self):
        init_db()
        session = create_session()

        context = build_context(
            session["id"],
            session["profile_id"],
            "诊断候选人回答",
        )

        assert "不是知识库问答助手" in context
        assert "面试题 + 候选人回答" in context
        assert "只用于对标候选人回答" in context

    def test_build_context_with_knowledge(self):
        init_db()
        session = create_session()

        context = build_context(
            session["id"],
            session["profile_id"],
            "诊断我的回答",
            knowledge_results=[
                {"title": "ReAct", "content": "ReAct is...", "dimension": "architecture", "score": 0.8},
            ],
        )
        assert "ReAct" in context
        assert "KNOWLEDGE" in context or "knowledge" in context.lower()

    def test_build_context_with_memory(self):
        init_db()
        session = create_session()
        save_memory(session["id"], "weakness", "short answers", "diagnosis")

        context = build_context(
            session["id"],
            session["profile_id"],
            "新问题",
            max_chars=10000,
        )
        assert "weakness" in context
        assert "short answers" in context

    def test_memory_summary_truncates(self):
        init_db()
        session = create_session()
        save_memory(session["id"], "weakness", "x" * 1000, "diagnosis")

        context = build_context(
            session["id"],
            session["profile_id"],
            "新问题",
            max_chars=10000,
        )
        assert "x" * 300 in context
        assert "x" * 301 not in context
        assert context.count("x") < 850

    def test_context_length_control(self):
        init_db()
        session = create_session()

        context = build_context(
            session["id"],
            session["profile_id"],
            "Hello",
            max_chars=500,
        )
        assert len(context) <= 600  # Allow some margin

    def test_memory_is_isolated_by_profile(self):
        init_db()
        first = create_session("00000000-0000-4000-8000-000000000011")
        second = create_session("00000000-0000-4000-8000-000000000022")
        save_memory(first["id"], "weakness", "需要补充边界条件", profile_id=first["profile_id"])

        own_context = build_context(first["id"], first["profile_id"], "新问题")
        other_context = build_context(second["id"], second["profile_id"], "新问题")
        assert "需要补充边界条件" in own_context
        assert "需要补充边界条件" not in other_context

class TestTrace:
    """Tests for trace recording."""

    def test_create_trace(self):
        init_db()
        session = create_session()
        trace = create_trace(session["id"])
        assert "id" in trace
        assert trace["status"] == "running"

    def test_add_trace_events(self):
        init_db()
        session = create_session()
        trace = create_trace(session["id"])
        add_trace_event(trace["id"], "tool_call", step_index=1, data={"tool": "search_knowledge"})
        add_trace_event(trace["id"], "tool_result", step_index=1, data={"result": "ok"})

        full_trace = get_trace(trace["id"])
        assert full_trace is not None
        assert len(full_trace["events"]) == 2
        assert full_trace["events"][0]["event_type"] == "tool_call"

    def test_complete_trace(self):
        init_db()
        session = create_session()
        trace = create_trace(session["id"])
        complete_trace(trace["id"])

        full_trace = get_trace(trace["id"])
        assert full_trace is not None
        assert full_trace["status"] == "completed"

    def test_get_nonexistent_trace(self):
        init_db()
        assert get_trace("nonexistent") is None

    def test_full_trace_link(self):
        """Trace should record a complete execution link."""
        init_db()
        session = create_session()
        trace = create_trace(session["id"])

        # Simulate a full flow
        events = [
            ("session_start", 0, {}),
            ("knowledge_retrieved", 1, {"query": "ReAct"}),
            ("tool_call", 2, {"tool": "diagnose_interview"}),
            ("tool_result", 2, {"result": {"total": 40}}),
            ("done", 3, {"final_output": "诊断报告"}),
        ]

        for evt_type, step, data in events:
            add_trace_event(trace["id"], evt_type, step_index=step, data=data)

        complete_trace(trace["id"])

        full_trace = get_trace(trace["id"])
        assert full_trace is not None
        assert len(full_trace["events"]) == 5
        types = [e["event_type"] for e in full_trace["events"]]
        assert "knowledge_retrieved" in types
        assert "tool_call" in types
        assert "tool_result" in types
        assert "done" in types


class TestEvals:
    """Tests for evaluation system."""

    def setup_method(self):
        init_db()

    def test_eval_cases_count(self):
        assert len(EVAL_CASES) >= 23

    def test_run_single_eval(self):
        result = run_eval("short_answer_structure")
        assert "eval_name" in result
        assert "passed" in result
        assert "details" in result

    def test_run_all_evals(self):
        results = run_all_evals()
        assert results["total"] >= 23
        assert results["passed"] >= 0
        assert "pass_rate" in results

    def test_short_answer_low_score(self):
        result = run_eval("short_answer_structure")
        assert result["passed"] is True, f"Failed: {result.get('reasons')}"

    def test_off_topic_low_alignment(self):
        result = run_eval("off_topic_structure")
        assert result["passed"] is True, f"Failed: {result.get('reasons')}"

    def test_filler_words_detected(self):
        result = run_eval("filler_words_structure")
        assert result["passed"] is True, f"Failed: {result.get('reasons')}"

    def test_good_answer_high_score(self):
        result = run_eval("good_answer_structure")
        assert result["passed"] is True, f"Failed: {result.get('reasons')}"

    def test_retrieval_fts(self):
        result = run_eval("eval_knowledge_recall_fts")
        assert result["passed"] is True, f"Failed: {result.get('reasons')}"

    def test_retrieval_semantic(self):
        result = run_eval("eval_knowledge_recall_semantic")
        # Semantic retrieval requires embedding; accept both pass (embedding available)
        # and embedding_unavailable recorded in details
        reasons = result.get("reasons", [])
        assert result["passed"] is True or "embedding" in str(reasons).lower(), f"Failed: {reasons}"

    def test_run_unknown_case(self):
        result = run_eval("nonexistent")
        assert "error" in result

    def test_save_eval_run(self):
        results = run_all_evals()
        saved = save_eval_run(results)
        assert "id" in saved
        assert "summary" in saved
