"""Diagnosis Run API and durable report regression tests."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace


def _new_session(client) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 200
    return response.json()["id"]


def _wait_terminal(client, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] in {"completed", "failed", "cancelled", "interrupted", "waiting_approval"}:
            return run
        time.sleep(0.02)
    raise AssertionError("Run did not reach a visible state")


def test_diagnosis_run_persists_events_report_summary_and_replays(client):
    session_id = _new_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "diagnosis-1"},
        json={"type": "diagnosis", "input": {"question": "什么是 ReAct？", "answer": "ReAct 结合推理、行动和观察，并通过工具调用、超时、重试边界与日志处理生产故障。"}},
    )
    assert response.status_code == 202
    run_id = response.json()["run"]["id"]
    run = _wait_terminal(client, run_id)
    assert run["status"] == "completed"
    assert "memory_candidates" not in run["result"]
    assert "memory_candidates" not in run["result"]["diagnosis"]
    events = client.get(f"/api/runs/{run_id}/events").json()["events"]
    assert all(event["run_id"] == run_id for event in events)
    assert [event["sequence"] for event in events] == sorted(event["sequence"] for event in events)
    assert any(event["type"] == "report_ready" for event in events)
    for event_type in [
        "knowledge_merged",
        "context_built",
        "diagnosis_model_started",
        "diagnosis_model_completed",
        "output_validated",
        "report_ready",
        "run_completed",
    ]:
        assert event_type in [event["type"] for event in events]
    assert next(event for event in events if event["type"] == "knowledge_merged")["data"]["duration_ms"] >= 0
    model_completed = next(event for event in events if event["type"] == "diagnosis_model_completed")["data"]
    assert set(model_completed) == {
        "stream_mode", "duration_ms", "first_token_ms", "input_tokens", "output_tokens",
        "total_tokens", "reasoning_tokens", "finish_reason", "generated_chars",
    }
    assert next(event for event in events if event["type"] == "output_validated")["data"]["success"] is True
    assert next(event for event in events if event["type"] == "report_ready")["data"]["duration_ms"] >= 0
    assert all(event["sequence"] > 1 for event in client.get(f"/api/runs/{run_id}/events?after=1").json()["events"])
    reports = client.get(f"/api/sessions/{session_id}/reports").json()["reports"]
    assert len(reports) == 1
    assert client.get(f"/api/sessions/{session_id}/reports/{reports[0]['id']}").json()["report_markdown"]
    assert client.get(f"/api/sessions/{session_id}/reports/not-found").status_code == 404
    assert client.get(f"/api/sessions/{session_id}/summary").json()["summary_version"] >= 1


def test_diagnosis_progress_and_fallback_events_are_durable_and_redacted(client, monkeypatch):
    import offerpilot.diagnosis.scoring as diagnosis_module

    base_completion = diagnosis_module.structured_json_completion

    def emitting_completion(**kwargs):
        kwargs["on_fallback"]({"reason": "stream_json_unsupported", "stream_mode": "non_stream_fallback"})
        kwargs["on_progress"]({"duration_ms": 5000, "generated_chars": 321})
        return base_completion(**kwargs)

    monkeypatch.setattr(diagnosis_module, "structured_json_completion", emitting_completion)
    session_id = _new_session(client)
    answer_canary = "CANARY-ANSWER-DO-NOT-LOG"
    response = client.post(
        f"/api/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "diagnosis-stage-events"},
        json={"type": "diagnosis", "input": {"question": "什么是 ReAct？", "answer": answer_canary}},
    )
    run_id = response.json()["run"]["id"]
    assert _wait_terminal(client, run_id)["status"] == "completed"
    events = client.get(f"/api/runs/{run_id}/events").json()["events"]
    fallback = next(event for event in events if event["type"] == "diagnosis_model_fallback")
    progress = next(event for event in events if event["type"] == "diagnosis_model_progress")
    assert fallback["data"] == {"reason": "stream_json_unsupported", "stream_mode": "non_stream_fallback"}
    assert progress["data"] == {"duration_ms": 5000, "generated_chars": 321}
    serialized = json.dumps(events, ensure_ascii=False)
    assert answer_canary not in serialized
    assert "exam_points" not in serialized


def test_invalid_diagnosis_model_output_persists_failed_validation_stage(client, monkeypatch):
    import offerpilot.diagnosis.scoring as diagnosis_module

    def invalid_completion(**_kwargs):
        return SimpleNamespace(
            data={"unexpected": True}, source="llm", stream_mode="stream", duration_ms=25,
            first_token_ms=5, input_tokens=None, output_tokens=None, total_tokens=None,
            reasoning_tokens=None, finish_reason="stop", generated_chars=19,
        )

    monkeypatch.setattr(diagnosis_module, "structured_json_completion", invalid_completion)
    session_id = _new_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "diagnosis-invalid-output"},
        json={"type": "diagnosis", "input": {"question": "什么是 ReAct？", "answer": "ReAct 是推理和行动循环。"}},
    )
    run_id = response.json()["run"]["id"]
    run = _wait_terminal(client, run_id)
    assert run["status"] == "failed"
    assert run["error_code"] == "llm_invalid_response"
    events = client.get(f"/api/runs/{run_id}/events").json()["events"]
    validation = next(event for event in events if event["type"] == "output_validated")
    assert validation["data"]["success"] is False
    assert validation["data"]["error_code"] == "llm_invalid_response"
    assert any(event["type"] == "diagnosis_model_completed" for event in events)
    assert not any(event["type"] == "report_ready" for event in events)
