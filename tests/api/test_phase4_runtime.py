"""Phase 4 regression coverage for deadlines, cancellation, and terminal consistency."""

from __future__ import annotations

import asyncio

import pytest

from offerpilot.agent.coach_loop import (
    CoachLoop,
    _failure_code,
    _render_read_only_tool_result,
    _tool_context,
    _tool_names_for_turn,
)
from offerpilot.agent.types import AgentConfig, ToolDefinition
from offerpilot.agent.registry import ToolRegistry
from offerpilot.coaching.coaching_api import _diagnosis_error_code, _diagnosis_stream, _guidance_stream, _looks_like_scoring_request
from offerpilot.coaching.state import begin_run, get_run, request_cancel
from offerpilot.core.database import get_db, init_db
from offerpilot.core.deadline import RunDeadlineExceeded, await_with_deadline, deadline_after
from offerpilot.core.errors import AppError
from offerpilot.diagnosis.diagnosis import (
    get_diagnosis_report,
    persist_completed_diagnosis,
    save_memory,
)
from offerpilot.session.session import create_session, get_recent_messages, get_session
from offerpilot.trace.trace_eval import get_trace


PROFILE_ID = "00000000-0000-4000-8000-000000000001"


def _running_session() -> tuple[dict, dict]:
    init_db()
    session = create_session(PROFILE_ID)
    run = begin_run(session["id"], PROFILE_ID, "coach")
    assert run is not None
    return session, run


def test_memory_write_requires_the_matching_running_trace():
    session, run = _running_session()
    request_cancel(session["id"], PROFILE_ID, run["trace_id"])

    with pytest.raises(RuntimeError, match="no longer running"):
        save_memory(
            session["id"],
            "weakness",
            "cancelled run must not write memory",
            profile_id=PROFILE_ID,
            trace_id=run["trace_id"],
        )

    assert get_recent_messages(session["id"]) == []
    conn = get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    finally:
        conn.close()


def test_diagnosis_completion_is_one_atomic_terminal_transaction():
    session, run = _running_session()
    saved = persist_completed_diagnosis(
        session_id=session["id"],
        profile_id=PROFILE_ID,
        trace_id=run["trace_id"],
        question="什么是 ReAct？",
        answer="它把推理与行动结合。",
        content_scores={"dimensions": {}},
        voice_scores={"dimensions": {}},
        overall_score=8.0,
        report_markdown="# 诊断报告",
        diagnosis={"exam_points": []},
        sources=["knowledge/test.md"],
    )

    assert saved is not None
    assert get_diagnosis_report(saved["id"]) is not None
    assert get_run(session["id"], PROFILE_ID, run["trace_id"])["status"] == "completed"
    assert get_trace(run["trace_id"])["status"] == "completed"
    assert get_session(session["id"])["status"] == "ready"
    assert any(message["content"] == "# 诊断报告" for message in get_recent_messages(session["id"]))


@pytest.mark.asyncio
async def test_deadline_cancels_a_slow_tool_without_waiting_past_budget():
    async def slow_operation() -> str:
        await asyncio.sleep(1)
        return "late"

    deadline = deadline_after(0.01)
    with pytest.raises(RunDeadlineExceeded):
        await await_with_deadline(slow_operation(), deadline=deadline)


@pytest.mark.asyncio
async def test_coach_tool_execution_uses_remaining_budget_and_never_retries_side_effects():
    calls = 0

    async def side_effect(_params: dict) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return "late"

    registry = ToolRegistry()
    registry.register(ToolDefinition("side_effect", "test", {}, "high", side_effect))
    loop = CoachLoop(session_id="session", profile_id=PROFILE_ID, trace_id="trace")
    loop._deadline = deadline_after(0.01)

    with pytest.raises(RunDeadlineExceeded):
        await loop._execute_tool(registry, "side_effect", {})
    assert calls == 1


@pytest.mark.asyncio
async def test_diagnosis_outer_budget_interrupts_retrieval_without_persisting(monkeypatch):
    import offerpilot.diagnosis.workflow as workflow

    session, run = _running_session()

    async def slow_retrieval(**_kwargs):
        await asyncio.sleep(1)
        return []

    monkeypatch.setattr(workflow, "search_knowledge_safe_async", slow_retrieval)
    with pytest.raises(AppError) as exc_info:
        await workflow.run_diagnosis(
            session_id=session["id"],
            profile_id=PROFILE_ID,
            trace_id=run["trace_id"],
            question="什么是 ReAct？",
            answer="它让模型在推理和行动间循环。",
            timeout=0.01,
        )
    assert exc_info.value.code == "diagnosis_timeout"
    conn = get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM diagnosis_reports").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sse_queue_backpressure_never_blocks_producer_or_terminal_persistence():
    loop = CoachLoop(session_id="session", profile_id=PROFILE_ID, trace_id="trace")
    loop._queue = asyncio.Queue(maxsize=1)
    loop._consumer_open = True

    await asyncio.wait_for(loop._emit("tool_result", result={"sequence": 1}), timeout=0.05)
    await asyncio.wait_for(loop._emit("run_complete", status="completed", success=True), timeout=0.05)

    queued = loop._queue.get_nowait()
    assert queued["type"] == "run_complete"
    loop._consumer_open = False
    await asyncio.wait_for(loop._emit("final_response", content="persisted without consumer"), timeout=0.05)


@pytest.mark.asyncio
async def test_diagnosis_stream_disconnect_during_startup_persists_cancellation():
    session, run = _running_session()

    class DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    response = _diagnosis_stream(
        http_request=DisconnectedRequest(),
        session_id=session["id"],
        profile_id=PROFILE_ID,
        trace_id=run["trace_id"],
        diagnosis=type("Diagnosis", (), {"question": "什么是 ReAct？", "answer": "它会调用工具。"})(),
        cancel_event=asyncio.Event(),
    )
    stream = response.body_iterator
    await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert get_run(session["id"], PROFILE_ID, run["trace_id"])["status"] == "cancelled"
    assert get_session(session["id"])["status"] == "ready"


@pytest.mark.asyncio
async def test_scoring_guidance_disconnect_releases_its_active_run():
    session, run = _running_session()

    class DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    response = _guidance_stream(
        DisconnectedRequest(), session["id"], PROFILE_ID, run["trace_id"]
    )
    with pytest.raises(StopAsyncIteration):
        await anext(response.body_iterator)
    assert get_run(session["id"], PROFILE_ID, run["trace_id"])["status"] == "cancelled"
    assert get_session(session["id"])["status"] == "ready"


def test_output_and_tool_context_budgets_are_enforced():
    loop = CoachLoop(
        session_id="session",
        profile_id=PROFILE_ID,
        trace_id="trace",
        config=AgentConfig(max_output_chars=5),
    )
    assert loop._truncate_output("123456") == "12345"
    assert loop._truncate_output("7") == ""
    context = _tool_context({"large": "x" * 20000})
    assert len(context) <= 12000
    assert "truncated" in context


def test_knowledge_tool_context_uses_field_level_compression():
    context = _tool_context([
        {
            "title": "ReAct",
            "question": "How does ReAct use tools?",
            "expert_answer": "x" * 3000,
            "content": "y" * 3000,
            "exam_points": ["point"] * 10,
            "source": "knowledge/react.md",
        }
        for _ in range(5)
    ])
    assert len(context) < 4000
    assert "y" * 1000 not in context
    assert '"truncated": true' in context


def test_read_only_knowledge_result_has_a_deterministic_bounded_reply():
    rendered = _render_read_only_tool_result(
        "search_knowledge",
        [{"title": "ReAct", "exam_points": ["reasoning", "tools"]}],
    )
    assert "ReAct" in rendered
    assert "reasoning" in rendered
    assert len(rendered) < 500


def test_coach_exposes_only_the_tool_needed_for_an_explicit_turn():
    assert _tool_names_for_turn([{"role": "user", "content": "Search the knowledge base for ReAct."}]) == {"search_knowledge"}
    assert _tool_names_for_turn([{"role": "user", "content": "Recommend a practice question."}]) == {"recommend_next_question"}
    assert _tool_names_for_turn([{"role": "user", "content": "Explain a concept."}]) == set()
    assert _tool_names_for_turn([
        {"role": "user", "content": "Search the knowledge base for ReAct."},
        {"role": "tool", "content": "retrieved result"},
    ]) == set()


def test_coach_failure_codes_do_not_expose_exception_text():
    error = RuntimeError("candidate answer must not appear in SSE errors")
    assert _failure_code(error) == "runtimeerror"
    assert _failure_code(type("ProviderFailure", (Exception,), {"category": "timeout"})()) == "timeout"


def test_diagnosis_failure_codes_do_not_expose_exception_text():
    error = RuntimeError("candidate answer must not appear in SSE errors")
    assert _diagnosis_error_code(error) == "runtimeerror"
    assert _diagnosis_error_code(type("ProviderFailure", (Exception,), {"category": "timeout"})()) == "timeout"


@pytest.mark.parametrize(
    "message",
    [
        "请给我的回答评分",
        "这段回答几分？",
        "评价一下候选人的表现",
        "帮我评估这个回答",
        "诊断我的面试答案",
        "rate my answer",
        "score this response",
        "evaluate the candidate response",
    ],
)
def test_scoring_intent_positive_corpus(message: str):
    assert _looks_like_scoring_request(message)


@pytest.mark.parametrize(
    "message",
    [
        "帮我准备一轮技术面试",
        "推荐一道 RAG 练习题",
        "解释 ReAct 的工具调用流程",
        "我想改善回答的结构",
        "列出我之前保存的练习偏好",
    ],
)
def test_scoring_intent_negative_corpus(message: str):
    assert not _looks_like_scoring_request(message)
