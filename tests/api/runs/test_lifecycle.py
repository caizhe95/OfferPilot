"""Recovery, cleanup, transcript, and deterministic summary contracts."""

from __future__ import annotations

import time
import uuid

import pytest

from offerpilot.audio.storage import get_audio_upload, resolve_uploaded_audio
from offerpilot.database.connection import get_db, init_db
from offerpilot.approvals.repository import claim_approval, create_approval, decide_approval, get_approval
from offerpilot.runs.repository import (
    claim_pending_run,
    create_run,
    events_after,
    get_run,
    transition_run,
)
from offerpilot.runs.recovery import list_resumable_runs, recover_runs
from offerpilot.profiles.memory_repository import save_memory
from offerpilot.sessions.followups import finish_followup_for_run, link_followup
from offerpilot.sessions.repository import add_audio_transcript, add_message, create_session, get_messages
from offerpilot.sessions.lifecycle import delete_session_with_assets
from offerpilot.sessions.summaries import rebuild_session_summary


PROFILE_ID = "00000000-0000-4000-8000-000000000001"
VALID_WAV = b"RIFF\x00\x00\x00\x00WAVE\x00\x00\x00\x00"


@pytest.fixture(autouse=True)
def initialized_database():
    init_db()


def _new_session(client) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 200
    return response.json()["id"]


def _wait_for_status(client, run_id: str, *statuses: str) -> dict:
    for _ in range(100):
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.02)
    raise AssertionError(f"Run did not reach one of {statuses}")


def _waiting_audio_run(client) -> tuple[str, str, dict, str]:
    session_id = _new_session(client)
    upload = client.post(
        f"/api/sessions/{session_id}/audio-uploads",
        files={"file": ("answer.wav", VALID_WAV, "audio/wav")},
    ).json()["upload"]
    created = client.post(
        f"/api/sessions/{session_id}/runs",
        headers={"Idempotency-Key": f"audio-{uuid.uuid4().hex}"},
        json={"type": "audio_transcription", "input": {"upload_id": upload["id"]}},
    )
    assert created.status_code == 202
    run_id = created.json()["run"]["id"]
    assert _wait_for_status(client, run_id, "waiting_approval")["status"] == "waiting_approval"
    events = client.get(f"/api/runs/{run_id}/events").json()["events"]
    approval_id = next(event for event in events if event["type"] == "approval_required")["data"]["approval_id"]
    return session_id, run_id, upload, approval_id


def test_cancel_waiting_audio_run_cancels_approval_and_removes_bytes(client):
    session_id, run_id, upload, approval_id = _waiting_audio_run(client)
    assert resolve_uploaded_audio(upload["storage_name"]).exists()

    cancelled = client.post(f"/api/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert _wait_for_status(client, run_id, "cancelled")["status"] == "cancelled"
    assert get_audio_upload(upload["id"], session_id, PROFILE_ID)["status"] == "cancelled"
    assert not resolve_uploaded_audio(upload["storage_name"]).exists()

    conn = get_db()
    try:
        approval = conn.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    finally:
        conn.close()
    assert approval is not None and approval["status"] == "failed"


def test_session_delete_cancels_waiting_audio_and_hard_deletes_content(client):
    session_id, run_id, upload, approval_id = _waiting_audio_run(client)
    assert client.delete(f"/api/sessions/{session_id}").status_code == 204
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert not resolve_uploaded_audio(upload["storage_name"]).exists()

    conn = get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM approvals WHERE id = ?", (approval_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM audio_uploads WHERE id = ?", (upload["id"],)).fetchone()[0] == 0
    finally:
        conn.close()


def test_recovery_interrupts_running_and_schedules_decided_approvals():
    running_session = create_session(PROFILE_ID)
    waiting_session = create_session(PROFILE_ID)
    running, _ = create_run(PROFILE_ID, running_session["id"], "coach", {"message": "练习"}, "restart-running")
    assert claim_pending_run(running["id"])["status"] == "running"

    waiting, _ = create_run(PROFILE_ID, waiting_session["id"], "report_export", {"report_id": "report-1"}, "restart-waiting")
    assert claim_pending_run(waiting["id"])["status"] == "running"
    approval_id = create_approval(
        waiting["id"], waiting_session["id"], PROFILE_ID, "export_report", "high", {"report_id": "report-1"}, "export"
    )
    transition_run(waiting["id"], "waiting_approval", state={"approval_id": approval_id})
    assert decide_approval(approval_id, PROFILE_ID, "approve")["decision"] == "approve"

    assert running["id"] in recover_runs()
    assert get_run(running["id"], PROFILE_ID)["status"] == "interrupted"
    assert any(event["type"] == "run_interrupted" for event in events_after(running["id"]))
    assert waiting["id"] in list_resumable_runs()


def test_deciding_expired_approval_closes_waiting_run_and_emits_terminal_event(monkeypatch):
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "report_export", {"report_id": "report-1"}, "expired-decision")
    approval_id = create_approval(
        run["id"], session["id"], PROFILE_ID, "export_report", "high", {"report_id": "report-1"}, "export"
    )
    transition_run(run["id"], "waiting_approval", state={"approval_id": approval_id})
    conn = get_db()
    try:
        conn.execute("UPDATE approvals SET expires_at = ? WHERE id = ?", ("2020-01-01T00:00:00+00:00", approval_id))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="approval_expired"):
        decide_approval(approval_id, PROFILE_ID, "approve")
    assert get_approval(approval_id, PROFILE_ID)["status"] == "expired"
    assert get_run(run["id"], PROFILE_ID)["status"] == "cancelled"
    assert events_after(run["id"])[-2]["type"] == "run_complete"
    assert events_after(run["id"])[-1]["type"] == "run_cancelled"


def test_claim_approval_rechecks_expiry_and_closes_run():
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "report_export", {"report_id": "report-1"}, "expired-claim")
    approval_id = create_approval(
        run["id"], session["id"], PROFILE_ID, "export_report", "high", {"report_id": "report-1"}, "export"
    )
    transition_run(run["id"], "waiting_approval", state={"approval_id": approval_id})
    assert decide_approval(approval_id, PROFILE_ID, "approve")["decision"] == "approve"
    conn = get_db()
    try:
        conn.execute("UPDATE approvals SET expires_at = ? WHERE id = ?", ("2020-01-01T00:00:00+00:00", approval_id))
        conn.commit()
    finally:
        conn.close()

    assert claim_approval(approval_id, PROFILE_ID) is None
    assert get_approval(approval_id, PROFILE_ID)["status"] == "expired"
    assert get_run(run["id"], PROFILE_ID)["status"] == "cancelled"


def test_audio_transcript_persistence_is_idempotent():
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "audio_transcription", {"upload_id": "upload-1"}, "transcript-run")
    first = add_audio_transcript(session["id"], run["id"], "第一份转写", "upload-1")
    repeated = add_audio_transcript(session["id"], run["id"], "不应重复写入", "upload-1")
    transcripts = [message for message in get_messages(session["id"]) if message["kind"] == "audio_transcript"]
    assert first["id"] == repeated["id"]
    assert [message["content"] for message in transcripts] == ["第一份转写"]


def test_followup_failure_restores_pending_state_and_session_delete_removes_memory():
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "diagnosis", {"question": "Q", "answer": "A"}, "followup-run")
    followup_id = str(uuid.uuid4())
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO session_followups(id, session_id, profile_id, question, reason, status, created_at, updated_at) VALUES(?, ?, ?, ?, ?, 'pending', ?, ?)",
            (followup_id, session["id"], PROFILE_ID, "请补充超时边界", "覆盖遗漏考点", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    assert link_followup(followup_id, session["id"], PROFILE_ID, run["id"])
    finish_followup_for_run(run["id"], PROFILE_ID, False)
    assert claim_pending_run(run["id"])["status"] == "running"
    approval_id = create_approval(
        run["id"], session["id"], PROFILE_ID, "save_memory", "high", {"key": "weakness"}, "coach"
    )
    assert decide_approval(approval_id, PROFILE_ID, "approve") is not None
    assert claim_approval(approval_id, PROFILE_ID) is not None
    memory = save_memory(session_id=session["id"], profile_id=PROFILE_ID, key="weakness", value="补充超时边界", category="diagnosis", run_id=run["id"])

    conn = get_db()
    try:
        followup = conn.execute("SELECT status FROM session_followups WHERE id = ?", (followup_id,)).fetchone()
    finally:
        conn.close()
    assert followup is not None and followup["status"] == "pending"

    assert delete_session_with_assets(session["id"], PROFILE_ID)
    conn = get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM profile_memories WHERE id = ?", (memory["id"],)).fetchone()[0] == 0
    finally:
        conn.close()


def _seed_point_result(session_id: str, point_id: str, status: str, index: int) -> None:
    run, _ = create_run(
        PROFILE_ID,
        session_id,
        "diagnosis",
        {"question": f"问题 {index}", "answer": f"回答 {index}"},
        f"summary-{index}",
    )
    assert claim_pending_run(run["id"])["status"] == "running"
    report_id = str(uuid.uuid4())
    created_at = f"2026-01-01T00:00:0{index}+00:00"
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO diagnosis_reports(id, run_id, session_id, profile_id, question, answer, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (report_id, run["id"], session_id, PROFILE_ID, f"问题 {index}", f"回答 {index}", created_at),
        )
        conn.execute(
            "INSERT INTO diagnosis_point_results(id, report_id, profile_id, exam_point_id, status, explanation, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), report_id, PROFILE_ID, point_id, status, "测试", created_at),
        )
        conn.commit()
    finally:
        conn.close()
    transition_run(run["id"], "completed")


def test_summary_reducer_tracks_weakness_mastery_and_regression_deterministically():
    session = create_session(PROFILE_ID)
    point_id = "agent-timeout-boundaries"
    _seed_point_result(session["id"], point_id, "partial", 1)
    _seed_point_result(session["id"], point_id, "missing", 2)
    first_message = add_message(session["id"], "user", "请总结我的问题")
    first = rebuild_session_summary(session["id"], PROFILE_ID)
    assert point_id in first["summary_json"]["recurring_weaknesses"]
    assert first["source_message_id"] == first_message["id"]

    _seed_point_result(session["id"], point_id, "covered", 3)
    _seed_point_result(session["id"], point_id, "covered", 4)
    mastered = rebuild_session_summary(session["id"], PROFILE_ID)
    assert point_id in mastered["summary_json"]["mastered_topics"]
    repeated = rebuild_session_summary(session["id"], PROFILE_ID)
    assert repeated["summary_json"] == mastered["summary_json"]
    assert repeated["summary_version"] > mastered["summary_version"]

    _seed_point_result(session["id"], point_id, "partial", 5)
    latest_message = add_message(session["id"], "user", "我又遗漏了超时边界")
    regressed = rebuild_session_summary(session["id"], PROFILE_ID)
    assert point_id not in regressed["summary_json"]["mastered_topics"]
    assert regressed["source_message_id"] > first["source_message_id"]
    assert regressed["source_message_id"] == latest_message["id"]
