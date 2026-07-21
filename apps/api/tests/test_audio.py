"""Tests for audio upload and ASR."""

import io
import asyncio
from types import SimpleNamespace
from app.core.database import init_db
from app.session.session import create_session
from app.audio.audio import (
    validate_audio,
    save_audio_file,
    save_transcript_to_session,
    MAX_AUDIO_SIZE,
    ALLOWED_AUDIO_TYPES,
    _extract_mimo_transcript,
    transcribe_audio,
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
        content = b"invalid audio data"
        filepath = save_audio_file(content, "test.wav", "audio/wav")
        assert filepath.endswith(".wav")
        assert "uploads" in filepath

        # Cleanup
        from pathlib import Path
        Path(filepath).unlink(missing_ok=True)

    def test_save_mp3_file(self):
        content = b"invalid mp3 data"
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


class TestMiMoProtocol:
    def test_extracts_supported_response_shapes(self):
        assert _extract_mimo_transcript({"text": "直接文本"}) == "直接文本"
        assert _extract_mimo_transcript({"choices": [{"message": {"content": "消息文本"}}]}) == "消息文本"
        assert _extract_mimo_transcript({"choices": [{"message": {"content": [{"text": "数组"}, {"text": "文本"}]}}]}) == "数组文本"

    def test_sends_mimo_chat_completion_payload(self, monkeypatch):
        from app.core.config import settings
        import httpx
        from pathlib import Path

        monkeypatch.setattr(settings, "mimo_api_key", "test-key")
        monkeypatch.setattr(settings, "mimo_base_url", "https://mimo.example/v1")
        audio_path = Path("data/mimo-protocol-test.wav")
        audio_path.parent.mkdir(exist_ok=True)
        audio_path.write_bytes(b"wav-data")
        captured = {}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "转写结果"}}]}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return Response()

        monkeypatch.setattr(httpx, "post", fake_post)
        try:
            result = asyncio.run(transcribe_audio(str(audio_path)))
            assert result["transcript"] == "转写结果"
            assert captured["url"] == "https://mimo.example/v1/chat/completions"
            assert captured["headers"]["api-key"] == "test-key"
            assert captured["json"]["model"] == settings.mimo_asr_model
            assert captured["json"]["asr_options"] == {"language": "auto"}
            assert captured["json"]["messages"][0]["content"][0]["input_audio"]["data"].startswith("data:audio/wav;base64,")
        finally:
            audio_path.unlink(missing_ok=True)


class TestAudioApiPermissionFlow:
    """Tests for upload -> approval -> resume transcription."""

    def test_upload_requires_permission_before_asr(self, client):
        session = create_session()
        resp = client.post(
            "/api/audio/upload",
            data={"session_id": session["id"]},
            files={"file": ("sample.wav", b"invalid wav data", "audio/wav")},
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
        """Invalid audio data naturally fails ASR.
        The test verifies the error-handling path is functional."""
        session = create_session()
        upload_resp = client.post(
            "/api/audio/upload",
            data={"session_id": session["id"]},
            files={"file": ("sample.wav", b"invalid wav data", "audio/wav")},
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
        # Invalid WAV data fails ASR -> asr_failed
        assert data["status"] == "asr_failed"
        assert "error" in data
        assert data["provider"] == "mimo"
