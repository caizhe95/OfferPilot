"""Managed audio upload and approval tests."""

import asyncio
from io import BytesIO

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from offerpilot.audio.storage import MAX_AUDIO_SIZE, begin_audio_transcription, complete_audio_upload, create_audio_upload, get_audio_upload, validate_audio, validate_audio_signature
from offerpilot.runs.repository import begin_run, create_run


VALID_WAV = b"RIFF\x00\x00\x00\x00WAVE\x00\x00\x00\x00"


def _new_session(client) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 200
    return response.json()["id"]


def test_audio_validation_requires_type_size_and_signature():
    assert validate_audio("audio/wav", 1000)[0]
    assert not validate_audio("video/mp4", 1000)[0]
    assert not validate_audio("audio/wav", MAX_AUDIO_SIZE + 1)[0]
    assert validate_audio_signature("audio/wav", VALID_WAV)[0]
    assert not validate_audio_signature("audio/wav", b"not audio")[0]


def test_audio_signature_can_identify_a_valid_upload_without_declared_mime(client):
    session_id = _new_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/audio-uploads",
        files={"file": ("answer.wav", VALID_WAV, "")},
    )
    assert response.status_code == 200
    assert response.json()["upload"]["content_type"] == "audio/wav"


def test_audio_signature_rejects_a_conflicting_declared_mime(client):
    session_id = _new_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/audio-uploads",
        files={"file": ("answer.wav", VALID_WAV, "audio/mpeg")},
    )
    assert response.status_code == 400


def test_audio_denial_deletes_managed_bytes(client):
    session_id = _new_session(client)
    upload = client.post(
        f"/api/sessions/{session_id}/audio-uploads",
        files={"file": ("answer.wav", VALID_WAV, "audio/wav")},
    )
    assert upload.status_code == 200
    upload_id = upload.json()["upload"]["id"]
    created = client.post(
        f"/api/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "audio-deny"},
        json={"type": "audio_transcription", "input": {"upload_id": upload_id}},
    )
    assert created.status_code == 202
    run_id = created.json()["run"]["id"]
    import time
    for _ in range(100):
        events = client.get(f"/api/runs/{run_id}/events").json()["events"]
        approval_event = next((event for event in events if event["type"] == "approval_required"), None)
        if approval_event:
            break
        time.sleep(0.02)
    assert approval_event is not None
    approval_id = approval_event["data"]["approval_id"]
    denied = client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "deny"})
    assert denied.status_code == 200
    from offerpilot.audio.storage import get_audio_upload, resolve_uploaded_audio

    row = get_audio_upload(upload_id, session_id, "00000000-0000-4000-8000-000000000001")
    assert row is not None and row["status"] == "cancelled"
    assert not resolve_uploaded_audio(row["storage_name"]).exists()


def _upload_file(content: bytes, content_type: str = "audio/wav") -> UploadFile:
    return UploadFile(file=BytesIO(content), filename="answer.wav", headers=Headers({"content-type": content_type}))


def test_upload_uses_bounded_reads_and_accepts_the_25mb_limit(client, monkeypatch):
    import offerpilot.audio.api as audio_api
    from offerpilot.audio.storage import finalize_audio_upload

    session_id = _new_session(client)
    payload = VALID_WAV + b"x" * (MAX_AUDIO_SIZE - len(VALID_WAV))
    upload_file = _upload_file(payload)
    read_sizes: list[int] = []
    original_read = UploadFile.read

    async def bounded_read(self, size: int = -1):
        read_sizes.append(size)
        assert size != -1
        assert size <= 64 * 1024
        return await original_read(self, size)

    monkeypatch.setattr(UploadFile, "read", bounded_read)
    response = asyncio.run(
        audio_api.upload_audio_endpoint(
            session_id,
            upload_file,
            "00000000-0000-4000-8000-000000000001",
        )
    )
    assert response["public"]["size"] == MAX_AUDIO_SIZE
    assert read_sizes and max(read_sizes) <= 64 * 1024
    assert finalize_audio_upload(
        response["upload"]["id"],
        session_id,
        "00000000-0000-4000-8000-000000000001",
        "deleted",
    )


def test_invalid_or_too_large_upload_never_creates_a_pending_record(client):
    from offerpilot.database.connection import get_db

    session_id = _new_session(client)
    invalid = client.post(
        f"/api/sessions/{session_id}/audio-uploads",
        files={"file": ("invalid.wav", b"not-a-wav", "audio/wav")},
    )
    too_large = client.post(
        f"/api/sessions/{session_id}/audio-uploads",
        files={"file": ("large.wav", VALID_WAV + b"x" * (MAX_AUDIO_SIZE + 1 - len(VALID_WAV)), "audio/wav")},
    )
    assert invalid.status_code == 400
    assert too_large.status_code == 400
    conn = get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM audio_uploads").fetchone()[0] == 0
    finally:
        conn.close()


def test_begin_audio_transcription_records_owning_run(client):
    from offerpilot.audio.storage import resolve_uploaded_audio

    session_id = _new_session(client)
    upload = create_audio_upload(
        session_id=session_id,
        profile_id="00000000-0000-4000-8000-000000000001",
        original_filename="answer.wav",
        content_type="audio/wav",
    )
    path = resolve_uploaded_audio(upload["storage_name"])
    path.write_bytes(VALID_WAV)
    assert complete_audio_upload(upload["id"], session_id, "00000000-0000-4000-8000-000000000001", len(VALID_WAV))
    run, _ = create_run(
        "00000000-0000-4000-8000-000000000001",
        session_id,
        "audio_transcription",
        {"upload_id": upload["id"]},
        "audio-run-link",
    )
    assert begin_run(run["id"])["status"] == "running"
    claimed = begin_audio_transcription(
        upload["id"], session_id, "00000000-0000-4000-8000-000000000001", run_id=run["id"]
    )
    assert claimed is not None and claimed["run_id"] == run["id"]
    assert get_audio_upload(upload["id"], session_id, "00000000-0000-4000-8000-000000000001")["run_id"] == run["id"]


def test_chunk_write_failure_marks_upload_failed_and_removes_pending_state(client, monkeypatch):
    import offerpilot.audio.api as audio_api
    from offerpilot.audio.storage import get_audio_upload

    class FailingFile:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, _chunk):
            raise OSError("injected write failure")

    class FailingPath:
        def open(self, _mode):
            return FailingFile()

    session_id = _new_session(client)
    monkeypatch.setattr(audio_api, "resolve_uploaded_audio", lambda _storage_name: FailingPath())
    with pytest.raises(OSError, match="injected write failure"):
        asyncio.run(
            audio_api.upload_audio_endpoint(
                session_id,
                _upload_file(VALID_WAV + b"data"),
                "00000000-0000-4000-8000-000000000001",
            )
        )
    from offerpilot.database.connection import get_db

    conn = get_db()
    try:
        upload_id = conn.execute("SELECT id FROM audio_uploads").fetchone()[0]
    finally:
        conn.close()
    row = get_audio_upload(upload_id, session_id, "00000000-0000-4000-8000-000000000001")
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] == "audio_upload_failed"


def test_cleanup_failure_is_observable_without_leaking_the_storage_path(client, monkeypatch):
    import json

    import offerpilot.audio.storage as uploads
    from offerpilot.audio.storage import finalize_audio_upload, get_audio_upload
    from offerpilot.database.connection import get_db

    session_id = _new_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/audio-uploads",
        files={"file": ("answer.wav", VALID_WAV, "audio/wav")},
    )
    assert response.status_code == 200
    upload_id = response.json()["upload"]["id"]
    upload = get_audio_upload(upload_id, session_id, "00000000-0000-4000-8000-000000000001")
    assert upload is not None
    original_delete = uploads.delete_uploaded_audio

    def cleanup_failure(_storage_name: str) -> bool:
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(uploads, "delete_uploaded_audio", cleanup_failure)
    assert finalize_audio_upload(upload_id, session_id, "00000000-0000-4000-8000-000000000001", "cancelled")
    row = get_audio_upload(upload_id, session_id, "00000000-0000-4000-8000-000000000001")
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["error"] == "audio_cleanup_failed"
    conn = get_db()
    try:
        log = conn.execute(
            "SELECT summary, metadata FROM operation_logs WHERE event_type = 'audio_cleanup_failed'"
        ).fetchone()
    finally:
        conn.close()
    assert log is not None
    assert upload["storage_name"] not in log["summary"]
    assert upload["storage_name"] not in json.dumps(log["metadata"])
    original_delete(upload["storage_name"])
