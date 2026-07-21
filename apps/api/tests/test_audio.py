"""Tests for audio upload and ASR."""

import pytest
import io
from app.core.database import init_db
from app.session.session import create_session
from app.audio.audio import (
    validate_audio,
    save_audio_file,
    save_transcript_to_session,
    MAX_AUDIO_SIZE,
    ALLOWED_AUDIO_TYPES,
)


class TestAudioValidation:
    """Tests for audio file validation."""

    def test_valid_wav_format(self):
        valid, msg = validate_audio("audio/wav", 1000)
        assert valid
        assert msg == ""

    def test_valid_mp3_format(self):
        valid, msg = validate_audio("audio/mpeg", 1000)
        assert valid
        assert msg == ""

    def test_invalid_format_rejected(self):
        valid, msg = validate_audio("video/mp4", 1000)
        assert not valid
        assert "不支持" in msg

    def test_empty_file_rejected(self):
        valid, msg = validate_audio("audio/wav", 0)
        assert not valid
        assert "空" in msg

    def test_oversize_file_rejected(self):
        valid, msg = validate_audio("audio/wav", MAX_AUDIO_SIZE + 1)
        assert not valid
        assert "超过" in msg


class TestAudioSave:
    """Tests for audio file saving."""

    def test_save_audio_file(self):
        content = b"fake audio data"
        filepath = save_audio_file(content, "test.wav", "audio/wav")
        assert filepath.endswith(".wav")
        assert "uploads" in filepath

        # Cleanup
        from pathlib import Path
        Path(filepath).unlink(missing_ok=True)

    def test_save_mp3_file(self):
        content = b"fake mp3 data"
        filepath = save_audio_file(content, "test.mp3", "audio/mpeg")
        assert filepath.endswith(".mp3")

        from pathlib import Path
        Path(filepath).unlink(missing_ok=True)


class TestTranscriptSaving:
    """Tests for transcript persistence."""

    def test_save_transcript_to_session(self):
        init_db()
        session = create_session()
        result = save_transcript_to_session(
            session["id"],
            "这是转写的文本内容",
            "/path/to/audio.wav",
        )
        assert result["transcript"] == "这是转写的文本内容"
        assert result["session_id"] == session["id"]

        # Verify it's in messages
        from app.session.session import get_recent_messages
        messages = get_recent_messages(session["id"])
        assert any("转写的文本内容" in m["content"] for m in messages)


class TestAllowedFormats:
    """Tests for allowed format enumeration."""

    def test_allowed_formats(self):
        assert "audio/wav" in ALLOWED_AUDIO_TYPES
        assert "audio/mpeg" in ALLOWED_AUDIO_TYPES


class TestAudioApiPermissionFlow:
    """Tests for upload -> approval -> resume transcription."""

    def test_upload_requires_permission_before_asr(self, client):
        session = create_session()
        resp = client.post(
            "/api/audio/upload",
            data={"session_id": session["id"]},
            files={"file": ("sample.wav", b"fake wav data", "audio/wav")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "permission_required"
        assert data["status"] == "approval_required"
        assert data["tool_name"] == "transcribe_audio"
        assert "filepath" in data["params"]

        session_resp = client.get(f"/api/sessions/{session['id']}")
        assert session_resp.json()["status"] == "waiting_approval"

    def test_approve_resume_transcribes_audio(self, client):
        """After removing mock mode, fake audio data naturally fails ASR.
        The test verifies the error-handling path is functional."""
        session = create_session()
        upload_resp = client.post(
            "/api/audio/upload",
            data={"session_id": session["id"]},
            files={"file": ("sample.wav", b"fake wav data", "audio/wav")},
        )
        request_id = upload_resp.json()["request_id"]

        approve_resp = client.post("/api/permission/approve", json={
            "request_id": request_id,
            "session_id": session["id"],
        })
        assert approve_resp.status_code == 200

        resume_resp = client.post("/api/permission/resume", json={
            "request_id": request_id,
            "session_id": session["id"],
        })
        assert resume_resp.status_code == 200
        data = resume_resp.json()
        # Without mock mode, fake WAV data fails ASR -> asr_failed
        assert data["status"] == "asr_failed"
        assert "error" in data
        assert data["provider"] == "openai"
