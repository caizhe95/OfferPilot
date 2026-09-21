"""MiMo ASR adapter for server-owned audio uploads."""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
from pathlib import Path
from time import perf_counter
from typing import Any

from offerpilot.core.config import settings
from offerpilot.core.deadlines import await_with_deadline, require_remaining
from offerpilot.core.logging import log_event
from offerpilot.audio.storage import resolve_uploaded_audio

logger = logging.getLogger(__name__)
ASR_TIMEOUT_SECONDS = 30.0


async def _await_asr_request(request: Any, cancel_event: asyncio.Event | None, deadline: float | None) -> Any:
    return await await_with_deadline(request, deadline=deadline, cancel_event=cancel_event)


def _asr_error_category(exc: Exception) -> str:
    import httpx

    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return "asr_timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "asr_rejected" if exc.response.status_code in {400, 401, 403, 413, 415, 422} else "asr_provider"
    if isinstance(exc, httpx.RequestError):
        return "asr_network"
    if isinstance(exc, (ValueError, OSError)):
        return "asr_invalid_input"
    return "asr_provider"


async def _build_asr_payload(storage_name: str, *, deadline: float | None, cancel_event: asyncio.Event | None) -> dict[str, Any]:
    managed_path = resolve_uploaded_audio(storage_name)
    audio_bytes = await await_with_deadline(asyncio.to_thread(managed_path.read_bytes), deadline=deadline, cancel_event=cancel_event)
    mime_type = mimetypes.guess_type(managed_path.name)[0] or "audio/wav"
    audio_data = await await_with_deadline(asyncio.to_thread(base64.b64encode, audio_bytes), deadline=deadline, cancel_event=cancel_event)
    encoded_audio = audio_data.decode("ascii")
    return {
        "model": settings.mimo_asr_model,
        "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": f"data:{mime_type};base64,{encoded_audio}"}}]}],
        "asr_options": {"language": "auto"},
    }


async def transcribe_audio(
    storage_name: str,
    *,
    cancel_event: asyncio.Event | None = None,
    timeout: float = ASR_TIMEOUT_SECONDS,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Transcribe a managed upload and return only safe result metadata."""
    started = perf_counter()
    try:
        if not settings.mimo_api_key.strip():
            result = {"transcript": "", "error": "asr_configuration", "provider": "mimo"}
            log_event(logger, logging.ERROR, "asr_call_failed", provider="mimo", error_code=result["error"], duration_ms=0)
            return result
        import httpx

        if cancel_event and cancel_event.is_set():
            return {"transcript": "", "error": "asr_cancelled", "provider": "mimo"}
        timeout = max(0.001, min(float(timeout), ASR_TIMEOUT_SECONDS, require_remaining(deadline)))
        headers = {"api-key": settings.mimo_api_key, "Authorization": f"Bearer {settings.mimo_api_key}"}
        async with asyncio.timeout(timeout):
            payload = await _build_asr_payload(storage_name, deadline=deadline, cancel_event=cancel_event)
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                response = await _await_asr_request(client.post(f"{settings.mimo_base_url.rstrip('/')}/chat/completions", headers=headers, json=payload), cancel_event, deadline)
                response.raise_for_status()
                data = response.json()
        if not isinstance(data, dict):
            raise ValueError("ASR response is not an object")
        transcript = _extract_mimo_transcript(data)
        if not transcript:
            result = {"transcript": "", "error": "asr_invalid_response", "provider": "mimo"}
            log_event(logger, logging.ERROR, "asr_call_failed", provider="mimo", error_code=result["error"], duration_ms=round((perf_counter() - started) * 1000, 1))
            return result
        result = {"transcript": transcript, "provider": "mimo", "duration_seconds": data.get("duration", 0), "language": data.get("language", "auto")}
        log_event(logger, logging.INFO, "asr_call_completed", provider="mimo", duration_ms=round((perf_counter() - started) * 1000, 1), result_code="ok")
        return result
    except asyncio.CancelledError:
        result = {"transcript": "", "error": "asr_cancelled", "provider": "mimo"}
        log_event(logger, logging.WARNING, "asr_call_failed", provider="mimo", error_code=result["error"], duration_ms=round((perf_counter() - started) * 1000, 1))
        return result
    except Exception as exc:
        result = {"transcript": "", "error": _asr_error_category(exc), "provider": "mimo"}
        log_event(logger, logging.ERROR, "asr_call_failed", provider="mimo", error_code=result["error"], duration_ms=round((perf_counter() - started) * 1000, 1))
        return result


def _extract_mimo_transcript(data: dict) -> str:
    direct = data.get("text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("text")).strip()
    return ""
