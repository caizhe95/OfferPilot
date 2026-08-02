"""Phase 2 contracts for atomic Runs, approvals, uploads, and recovery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from offerpilot.audio.audio import (
    complete_audio_upload,
    create_audio_upload,
    expire_audio_for_approvals,
    get_audio_upload,
    resolve_uploaded_audio,
)
from offerpilot.coaching.state import (
    begin_coach_resume,
    begin_run,
    create_approval,
    expire_stale_approvals,
    get_approval,
    get_run,
    resolve_approval,
    sweep_runtime_state,
)
from offerpilot.core.database import get_db, init_db
from offerpilot.session.session import create_session, get_session, transition_session

PROFILE_ONE = "00000000-0000-4000-8000-000000000001"
PROFILE_TWO = "00000000-0000-4000-8000-000000000002"


def _waiting_coach_approval() -> tuple[dict, dict, str]:
    session = create_session(PROFILE_ONE)
    run = begin_run(session["id"], PROFILE_ONE, "coach")
    assert run is not None
    request_id = create_approval(
        session["id"],
        PROFILE_ONE,
        "save_memory",
        "high",
        {"key": "weakness", "value": "补充边界条件", "category": "diagnosis"},
        flow_kind="coach",
        trace_id=run["trace_id"],
        public_params={"key": "weakness", "category": "diagnosis"},
    )
    from offerpilot.coaching.state import save_run

    save_run(
        session["id"],
        PROFILE_ONE,
        run["trace_id"],
        "waiting_approval",
        {"run_kind": "coach", "request_id": request_id, "messages": [], "pending_call": {}},
        run_kind="coach",
    )
    transition_session(session["id"], "waiting_approval")
    return session, run, request_id


def test_begin_run_allows_exactly_one_concurrent_start():
    init_db()
    session = create_session(PROFILE_ONE)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: begin_run(session["id"], PROFILE_ONE, "coach"), range(8)))
    started = [result for result in results if result is not None]
    assert len(started) == 1
    assert get_session(session["id"])["status"] == "running"
    assert get_run(session["id"], PROFILE_ONE, started[0]["trace_id"])["status"] == "running"


def test_approval_decision_and_coach_resume_are_idempotent_under_race():
    init_db()
    session, run, request_id = _waiting_coach_approval()
    first = resolve_approval(request_id, session["id"], PROFILE_ONE, "approved")
    second = resolve_approval(request_id, session["id"], PROFILE_ONE, "approved")
    assert first is not None and second is not None
    assert first["status"] == second["status"] == "approved"
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(
            executor.map(
                lambda _: begin_coach_resume(session["id"], PROFILE_ONE, request_id),
                range(6),
            )
        )
    resumed = [result for result in results if result is not None]
    assert len(resumed) == 1
    assert resumed[0][0]["trace_id"] == run["trace_id"]
    assert get_session(session["id"])["status"] == "running"


def test_expired_audio_approval_removes_only_its_managed_upload():
    init_db()
    session = create_session(PROFILE_ONE)
    upload = create_audio_upload(
        session_id=session["id"],
        profile_id=PROFILE_ONE,
        original_filename="sample.wav",
        content_type="audio/wav",
    )
    upload_path = resolve_uploaded_audio(upload["storage_name"])
    upload_path.write_bytes(b"managed bytes")
    assert complete_audio_upload(upload["id"], session["id"], PROFILE_ONE, upload_path.stat().st_size) is not None
    request_id = create_approval(
        session["id"],
        PROFILE_ONE,
        "transcribe_audio",
        "medium",
        {"upload_id": upload["id"]},
        flow_kind="audio",
        public_params={"filename": "sample.wav", "content_type": "audio/wav", "size": upload_path.stat().st_size},
        expires_hours=0,
    )
    expired = expire_stale_approvals()
    assert [item["id"] for item in expired] == [request_id]
    assert expire_audio_for_approvals(expired) == 1
    assert not upload_path.exists()
    assert get_audio_upload(upload["id"], session["id"], PROFILE_ONE)["status"] == "expired"
    assert get_approval(request_id, session["id"], PROFILE_ONE)["status"] == "expired"


def test_runtime_sweep_releases_orphaned_running_session():
    init_db()
    session = create_session(PROFILE_ONE)
    transition_session(session["id"], "running")
    cleaned = sweep_runtime_state()
    assert cleaned["released_sessions"] == 1
    assert get_session(session["id"])["status"] == "ready"


def test_audio_upload_and_state_are_profile_scoped_and_redacted(client):
    session = create_session(PROFILE_ONE)
    upload = create_audio_upload(
        session_id=session["id"],
        profile_id=PROFILE_ONE,
        original_filename="private.wav",
        content_type="audio/wav",
    )
    request_id = create_approval(
        session["id"],
        PROFILE_ONE,
        "transcribe_audio",
        "medium",
        {"upload_id": upload["id"]},
        flow_kind="audio",
        public_params={"filename": "private.wav", "content_type": "audio/wav", "size": 0},
    )
    state = client.get(f"/api/coach/state?session_id={session['id']}")
    assert state.status_code == 200
    approval = next(item for item in state.json()["approvals"] if item["request_id"] == request_id)
    assert approval["params"] == {"filename": "private.wav", "content_type": "audio/wav", "size": 0}
    assert "upload_id" not in approval["params"]
    assert get_audio_upload(upload["id"], session["id"], PROFILE_TWO) is None


def test_backup_restore_preserves_durable_rows():
    from offerpilot.core.config import settings

    init_db()
    session = create_session(PROFILE_ONE)
    source = get_db()
    backup_path = settings.db_path.parent / "offerpilot-phase2-backup-test.db"
    backup_path.unlink(missing_ok=True)
    destination = None
    try:
        import sqlite3

        destination = sqlite3.connect(backup_path)
        source.backup(destination)
    finally:
        if destination:
            destination.close()
        source.close()
    restored = __import__("sqlite3").connect(backup_path)
    try:
        row = restored.execute("SELECT id, profile_id, status FROM sessions WHERE id = ?", (session["id"],)).fetchone()
        assert row == (session["id"], PROFILE_ONE, "ready")
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        restored.close()
        backup_path.unlink(missing_ok=True)
