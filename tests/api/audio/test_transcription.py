"""ASR cancellation, timeout, and error classification contracts."""

import asyncio
from types import SimpleNamespace

import pytest

from offerpilot.audio import transcription
from offerpilot.core.config import settings


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


@pytest.mark.asyncio
async def test_asr_cancellation_returns_stable_cancelled_result(monkeypatch):
    monkeypatch.setattr(settings, "mimo_api_key", "test-key")
    monkeypatch.setattr(settings, "mimo_base_url", "https://example.invalid")
    started = asyncio.Event()
    monkeypatch.setattr(transcription, "_build_asr_payload", lambda *args, **kwargs: asyncio.sleep(0, result={"messages": []}))
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: _PendingClient(started))
    cancel_event = asyncio.Event()
    task = asyncio.create_task(transcription.transcribe_audio("audio.wav", cancel_event=cancel_event, timeout=10))
    await started.wait()
    cancel_event.set()
    result = await task
    assert result["error"] == "asr_cancelled"
    assert result["transcript"] == ""


@pytest.mark.asyncio
async def test_asr_timeout_is_classified_without_leaking_provider_details(monkeypatch):
    monkeypatch.setattr(settings, "mimo_api_key", "test-key")
    monkeypatch.setattr(settings, "mimo_base_url", "https://example.invalid")
    started = asyncio.Event()
    monkeypatch.setattr(transcription, "_build_asr_payload", lambda *args, **kwargs: asyncio.sleep(0, result={"messages": []}))
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: _PendingClient(started))
    result = await transcription.transcribe_audio("audio.wav", timeout=0.01)
    assert result == {"transcript": "", "error": "asr_timeout", "provider": "mimo"}
