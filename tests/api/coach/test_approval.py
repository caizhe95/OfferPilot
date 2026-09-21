"""Coach approval integration regression tests."""

from __future__ import annotations

import time

from offerpilot.llm.chat import ToolChatResult


def _new_session(client) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 200
    return response.json()["id"]


def _wait_terminal(client, run_id: str) -> dict:
    for _ in range(100):
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in {"completed", "failed", "cancelled", "interrupted", "waiting_approval"}:
            return run
        time.sleep(0.02)
    raise AssertionError("Run did not reach a visible state")


def test_coach_approval_persists_canonical_id_and_resumes(client, monkeypatch):
    import offerpilot.coach.loop as coach_loop
    from offerpilot.database.connection import get_db

    def fake_completion(*, messages, **_kwargs):
        if any(message.get("role") == "tool" for message in messages):
            return ToolChatResult(content="记忆已保存。", tool_calls=[])
        return ToolChatResult(
            content="",
            tool_calls=[{
                "id": "save-memory-call",
                "name": "save_memory",
                "arguments": '{"key":"weakness","value":"补充幂等设计","category":"diagnosis"}',
            }],
        )

    monkeypatch.setattr(coach_loop, "tool_chat_completion", fake_completion)
    session_id = _new_session(client)
    created = client.post(
        f"/api/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "coach-memory"},
        json={"type": "coach", "input": {"message": "请保存记忆：我需要补充幂等设计"}},
    )
    assert created.status_code == 202
    run_id = created.json()["run"]["id"]
    assert _wait_terminal(client, run_id)["status"] == "waiting_approval"
    events = client.get(f"/api/runs/{run_id}/events").json()["events"]
    approval_event = next(event for event in events if event["type"] == "approval_required")
    approval_id = approval_event["data"]["approval_id"]
    assert approval_id
    assert "request_id" not in approval_event["data"]

    approved = client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "approve"})
    assert approved.status_code == 200
    assert approved.json()["approval"]["decision_reused"] is False
    assert "params" not in approved.json()["approval"]
    assert _wait_terminal(client, run_id)["status"] == "completed"

    conn = get_db()
    try:
        memory = conn.execute("SELECT key, value, approval_id FROM profile_memories").fetchone()
        audits = conn.execute(
            "SELECT event_type, profile_id, run_id, summary, metadata FROM operation_logs WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    assert memory is not None
    assert dict(memory) == {"key": "weakness", "value": "补充幂等设计", "approval_id": approval_id}
    assert [audit["event_type"] for audit in audits] == ["permission_request", "permission_approve", "permission_execute"]
    assert all(audit["profile_id"] and audit["run_id"] == run_id for audit in audits)
    assert all("补充幂等设计" not in f"{audit['summary']} {audit['metadata']}" for audit in audits)
