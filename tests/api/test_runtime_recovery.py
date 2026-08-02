"""Runtime recovery, streaming and cancellation contracts."""

from __future__ import annotations

import asyncio
import json

import pytest

from offerpilot.agent.coach_loop import CoachLoop
from offerpilot.coaching.coaching_api import _diagnosis_stream
from offerpilot.coaching.state import get_run, recover_interrupted_runs, save_run
from offerpilot.core.database import get_db, init_db
from offerpilot.llm.llm_client import ToolChatResult
from offerpilot.main import app
from offerpilot.session.session import create_session, get_session, transition_session
from offerpilot.trace.trace_eval import create_trace, get_trace

PROFILE_ID = "00000000-0000-4000-8000-000000000001"


def _make_running_run() -> tuple[dict, str]:
    init_db()
    session = create_session(PROFILE_ID)
    transition_session(session["id"], "running")
    trace_id = create_trace(session["id"])["id"]
    save_run(session["id"], PROFILE_ID, trace_id, "running", {"run_kind": "coach"})
    return session, trace_id


def test_startup_recovery_interrupts_only_stale_active_runs():
    stale, stale_trace = _make_running_run()
    waiting = create_session(PROFILE_ID)
    transition_session(waiting["id"], "running")
    waiting_trace = create_trace(waiting["id"])["id"]
    save_run(waiting["id"], PROFILE_ID, waiting_trace, "waiting_approval", {"request_id": "approval"})
    transition_session(waiting["id"], "waiting_approval")
    conn = get_db()
    try:
        conn.execute("UPDATE coach_runs SET updated_at = ? WHERE session_id = ?", ("2000-01-01T00:00:00+00:00", stale["id"]))
        conn.commit()
    finally:
        conn.close()

    recovered = recover_interrupted_runs(max_age_seconds=90)

    assert [run["session_id"] for run in recovered] == [stale["id"]]
    assert get_run(stale["id"], PROFILE_ID, stale_trace)["status"] == "failed"
    assert get_session(stale["id"])["status"] == "ready"
    assert get_trace(stale_trace)["status"] == "failed"
    assert any(event["event_type"] == "run_recovered" for event in get_trace(stale_trace)["events"])
    assert get_run(waiting["id"], PROFILE_ID, waiting_trace)["status"] == "waiting_approval"
    assert get_session(waiting["id"])["status"] == "waiting_approval"


def test_application_lifespan_runs_stale_run_recovery():
    session, trace_id = _make_running_run()
    conn = get_db()
    try:
        conn.execute("UPDATE coach_runs SET updated_at = ? WHERE session_id = ?", ("2000-01-01T00:00:00+00:00", session["id"]))
        conn.commit()
    finally:
        conn.close()

    from fastapi.testclient import TestClient

    with TestClient(app):
        assert get_run(session["id"], PROFILE_ID, trace_id)["status"] == "failed"
        assert get_session(session["id"])["status"] == "ready"


@pytest.mark.asyncio
async def test_coach_streams_first_event_and_heartbeat_before_slow_provider_finishes(monkeypatch):
    session, trace_id = _make_running_run()
    provider_started = asyncio.Event()
    provider_release = asyncio.Event()

    async def slow_provider(**_kwargs):
        provider_started.set()
        await provider_release.wait()
        return ToolChatResult(content="完成", tool_calls=[])

    monkeypatch.setattr("offerpilot.agent.coach_loop.tool_chat_completion", slow_provider)
    loop = CoachLoop(session_id=session["id"], profile_id=PROFILE_ID, trace_id=trace_id)
    stream = loop.stream(heartbeat_seconds=0.01)

    first = await asyncio.wait_for(anext(stream), timeout=0.2)
    assert first["type"] == "session_start"
    await asyncio.wait_for(provider_started.wait(), timeout=0.2)
    heartbeat = await asyncio.wait_for(anext(stream), timeout=0.2)
    assert heartbeat["type"] == "ping"

    await stream.aclose()
    assert loop.result is not None
    assert loop.result.success is False
    assert get_run(session["id"], PROFILE_ID, trace_id)["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancelled_diagnosis_emits_terminal_event_and_recovers_session(monkeypatch):
    session, trace_id = _make_running_run()
    cancel_event = asyncio.Event()

    async def slow_diagnosis(**kwargs):
        await kwargs["cancel_event"].wait()
        raise asyncio.CancelledError()

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    monkeypatch.setattr("offerpilot.coaching.coaching_api.run_diagnosis", slow_diagnosis)
    response = _diagnosis_stream(
        http_request=ConnectedRequest(),
        session_id=session["id"],
        profile_id=PROFILE_ID,
        trace_id=trace_id,
        diagnosis=type("Diagnosis", (), {"question": "什么是 ReAct？", "answer": "它会调用工具。"})(),
        cancel_event=cancel_event,
    )
    stream = response.body_iterator
    assert json.loads((await anext(stream)).removeprefix("data: "))["type"] == "session_start"
    assert json.loads((await anext(stream)).removeprefix("data: "))["type"] == "diagnosis_started"
    terminal = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    cancel_event.set()
    event = json.loads((await asyncio.wait_for(terminal, timeout=0.2)).removeprefix("data: "))
    assert event["type"] == "run_complete"
    assert event["status"] == "cancelled"
    assert get_session(session["id"])["status"] == "ready"
