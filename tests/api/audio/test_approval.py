"""Audio approval decision regression tests."""

from __future__ import annotations

import time


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


def test_approval_decision_is_idempotent_and_rejects_conflicts(client):
    from offerpilot.audio.storage import get_audio_upload

    session_id = _new_session(client)
    upload = client.post(
        f"/api/sessions/{session_id}/audio-uploads",
        files={"file": ("answer.wav", b"RIFF\x00\x00\x00\x00WAVE\x00\x00\x00\x00", "audio/wav")},
    ).json()["upload"]
    created = client.post(
        f"/api/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "audio-decision"},
        json={"type": "audio_transcription", "input": {"upload_id": upload["id"]}},
    )
    run_id = created.json()["run"]["id"]
    assert _wait_terminal(client, run_id)["status"] == "waiting_approval"
    events = client.get(f"/api/runs/{run_id}/events").json()["events"]
    approval_id = next(event for event in events if event["type"] == "approval_required")["data"]["approval_id"]

    first = client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "deny"})
    repeated = client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "deny"})
    conflict = client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "approve"})
    assert first.status_code == 200 and first.json()["approval"]["decision_reused"] is False
    assert repeated.status_code == 200 and repeated.json()["approval"]["decision_reused"] is True
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "approval_decision_conflict"
    assert _wait_terminal(client, run_id)["status"] == "cancelled"
    assert get_audio_upload(upload["id"], session_id, "00000000-0000-4000-8000-000000000001")["status"] == "cancelled"
    from offerpilot.database.connection import get_db

    conn = get_db()
    try:
        logs = conn.execute(
            "SELECT event_type, summary, metadata FROM operation_logs WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    assert [row["event_type"] for row in logs] == ["permission_request", "permission_deny"]
    assert all(upload["id"] not in f"{row['summary']} {row['metadata']}" for row in logs)


def test_medium_approval_is_created_again_for_the_same_session(client):
    session_id = _new_session(client)
    approval_ids: list[str] = []
    for index in range(2):
        upload = client.post(
            f"/api/sessions/{session_id}/audio-uploads",
            files={"file": (f"answer-{index}.wav", b"RIFF\x00\x00\x00\x00WAVE\x00\x00\x00\x00", "audio/wav")},
        ).json()["upload"]
        created = client.post(
            f"/api/sessions/{session_id}/runs",
            headers={"Idempotency-Key": f"medium-repeat-{index}"},
            json={"type": "audio_transcription", "input": {"upload_id": upload["id"]}},
        )
        run_id = created.json()["run"]["id"]
        assert _wait_terminal(client, run_id)["status"] == "waiting_approval"
        events = client.get(f"/api/runs/{run_id}/events").json()["events"]
        approval_ids.append(next(event for event in events if event["type"] == "approval_required")["data"]["approval_id"])
        client.post(f"/api/approvals/{approval_ids[-1]}/decision", json={"decision": "deny"})
    assert len(set(approval_ids)) == 2
