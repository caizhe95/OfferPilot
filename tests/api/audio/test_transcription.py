"""ASR cancellation, timeout, and error classification contracts."""

import asyncio
from types import SimpleNamespace

import pytest

from offerpilot.audio import transcription
from offerpilot.core.config import settings

PROFILE_ID = "00000000-0000-4000-8000-000000000001"


class _PendingClient:
    def __init__(self, started: asyncio.Event) -> None:
        self.started = started
        self.chat = SimpleNamespace()

    async def post(self, *_args, **_kwargs):
        self.started.set()
        await asyncio.Future()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _ImmediateClient:
    def __init__(self, response) -> None:
        self.response = response

    async def post(self, *_args, **_kwargs):
        return self.response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _run_context() -> tuple[dict, dict]:
    from offerpilot.database.connection import init_db
    from offerpilot.runs.repository import create_run
    from offerpilot.sessions.repository import create_session

    init_db()
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "audio_transcription", {"upload_id": "test"}, "asr-ledger")
    return session, run


@pytest.mark.asyncio
async def test_asr_cancellation_returns_stable_cancelled_result(monkeypatch):
    monkeypatch.setattr(settings, "mimo_api_key", "test-key")
    monkeypatch.setattr(settings, "mimo_base_url", "https://example.invalid")
    started = asyncio.Event()
    monkeypatch.setattr(transcription, "_build_asr_payload", lambda *args, **kwargs: asyncio.sleep(0, result={"messages": []}))
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: _PendingClient(started))
    session, run = _run_context()
    cancel_event = asyncio.Event()
    task = asyncio.create_task(transcription.transcribe_audio(
        "audio.wav", cancel_event=cancel_event, timeout=10,
        run_id=run["id"], session_id=session["id"], profile_id=PROFILE_ID,
    ))
    await started.wait()
    cancel_event.set()
    result = await task
    assert result["error"] == "asr_cancelled"
    assert result["transcript"] == ""
    from offerpilot.runs.calls import list_calls
    assert list_calls(run["id"], PROFILE_ID)[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_asr_timeout_is_classified_without_leaking_provider_details(monkeypatch):
    monkeypatch.setattr(settings, "mimo_api_key", "test-key")
    monkeypatch.setattr(settings, "mimo_base_url", "https://example.invalid")
    started = asyncio.Event()
    monkeypatch.setattr(transcription, "_build_asr_payload", lambda *args, **kwargs: asyncio.sleep(0, result={"messages": []}))
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: _PendingClient(started))
    session, run = _run_context()
    result = await transcription.transcribe_audio(
        "audio.wav", timeout=0.01,
        run_id=run["id"], session_id=session["id"], profile_id=PROFILE_ID,
    )
    assert result == {"transcript": "", "error": "asr_timeout", "provider": "mimo"}
    from offerpilot.runs.calls import list_calls
    call = list_calls(run["id"], PROFILE_ID)[0]
    assert call["status"] == "failed" and call["error_category"] == "asr_timeout"


@pytest.mark.asyncio
async def test_asr_success_records_audio_duration(monkeypatch):
    monkeypatch.setattr(settings, "mimo_api_key", "test-key")
    monkeypatch.setattr(settings, "mimo_base_url", "https://example.invalid")
    monkeypatch.setattr(transcription, "_build_asr_payload", lambda *args, **kwargs: asyncio.sleep(0, result={"messages": []}))
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"text": "transcript", "duration": 12.5, "language": "zh"},
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: _ImmediateClient(response))
    session, run = _run_context()
    result = await transcription.transcribe_audio(
        "audio.wav", run_id=run["id"], session_id=session["id"], profile_id=PROFILE_ID,
    )
    assert result["transcript"] == "transcript"
    from offerpilot.runs.calls import list_calls
    call = list_calls(run["id"], PROFILE_ID)[0]
    assert call["status"] == "succeeded" and call["audio_seconds"] == 12.5
    assert call["attempt_count"] == 1 and call["unknown_reason"] == "price_unknown"
