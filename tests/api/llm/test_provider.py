"""Tests for provider retry, Coach chat, and structured JSON boundaries."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from offerpilot.core.config import settings
from offerpilot.llm import provider, structured
from offerpilot.llm.chat import MAX_CHAT_OUTPUT_TOKENS, MAX_TOOL_CALL_TOKENS, tool_chat_completion


class AsyncChunks:
    def __init__(self, chunks, error: Exception | None = None):
        self.chunks, self.error = list(chunks), error

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.chunks:
            return self.chunks.pop(0)
        if self.error:
            error, self.error = self.error, None
            raise error
        raise StopAsyncIteration


def json_chunk(content: str | None = None, *, finish_reason: str | None = None, usage=None):
    choices = [] if content is None and finish_reason is None else [SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)]
    return SimpleNamespace(choices=choices, usage=usage)


@pytest.mark.asyncio
async def test_structured_completion_requires_key(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    with pytest.raises(provider.LLMUnavailableError):
        await structured.structured_json_completion(task_name="unit", system_prompt="JSON", user_payload={})


@pytest.mark.asyncio
async def test_structured_stream_reassembles_json_and_usage(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    raw = json.dumps({"text": "中文", "ok": True}, ensure_ascii=True)
    usage = SimpleNamespace(prompt_tokens=23, completion_tokens=11, total_tokens=34, completion_tokens_details=SimpleNamespace(reasoning_tokens=4))

    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace())
        async def close(self): pass

    async def create(**kwargs):
        assert kwargs["stream"] is True and kwargs["max_tokens"] == 1200
        return AsyncChunks([*(json_chunk(character) for character in raw), json_chunk(finish_reason="stop"), json_chunk(usage=usage)])

    client = Client()
    client.chat.completions.create = create
    monkeypatch.setattr(provider, "create_client", lambda **_: client)
    result = await structured.structured_json_completion(task_name="unit", system_prompt="JSON", user_payload={})
    assert result.data == {"text": "中文", "ok": True}
    assert result.stream_mode == "stream"
    assert (result.input_tokens, result.output_tokens, result.total_tokens, result.reasoning_tokens) == (23, 11, 34, 4)


@pytest.mark.asyncio
async def test_structured_completion_falls_back_only_when_stream_json_is_unsupported(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    calls: list[bool] = []
    fallback_events: list[dict] = []

    class UnsupportedStream(Exception):
        status_code = 400

    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace())

        async def close(self):
            pass

    async def create(**kwargs):
        calls.append(bool(kwargs.get("stream")))
        if kwargs.get("stream"):
            raise UnsupportedStream("stream does not support response_format json_object")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'), finish_reason="stop")],
            usage=None,
        )

    def create_client(**_kwargs):
        client = Client()
        client.chat.completions.create = create
        return client

    monkeypatch.setattr(provider, "create_client", create_client)
    result = await structured.structured_json_completion(
        task_name="unit",
        system_prompt="JSON",
        user_payload={},
        on_fallback=fallback_events.append,
    )

    assert calls == [True, False]
    assert result.data == {"ok": True}
    assert result.stream_mode == "non_stream_fallback"
    assert fallback_events == [
        {"reason": "stream_json_unsupported", "stream_mode": "non_stream_fallback"}
    ]


@pytest.mark.asyncio
async def test_structured_completion_does_not_fallback_for_other_invalid_requests(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    fallback_events: list[dict] = []

    class InvalidRequest(Exception):
        status_code = 400

    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace())

        async def close(self):
            pass

    async def create(**_kwargs):
        raise InvalidRequest("invalid model")

    client = Client()
    client.chat.completions.create = create
    monkeypatch.setattr(provider, "create_client", lambda **_: client)

    with pytest.raises(provider.LLMUnavailableError):
        await structured.structured_json_completion(
            task_name="unit",
            system_prompt="JSON",
            user_payload={},
            on_fallback=fallback_events.append,
        )
    assert fallback_events == []


@pytest.mark.asyncio
async def test_provider_retries_and_honors_retry_after(monkeypatch):
    calls, delays = [], []
    class RateLimited(Exception):
        status_code = 429
        response = SimpleNamespace(headers={"retry-after": "0.25"})
    async def operation(timeout):
        calls.append(timeout)
        if len(calls) == 1: raise RateLimited()
        return "ok"
    async def no_wait(delay, _): delays.append(delay)
    monkeypatch.setattr(provider, "sleep_or_cancel", no_wait)
    assert await provider.request_with_retry(operation, timeout=10) == "ok"
    assert delays == [0.25]


@pytest.mark.asyncio
async def test_provider_retries_three_times_with_four_second_attempt_cap(monkeypatch):
    timeouts: list[float] = []
    delays: list[float] = []

    async def operation(timeout: float):
        timeouts.append(timeout)
        if len(timeouts) < 3:
            raise TimeoutError()
        return "ok"

    async def no_wait(delay: float, _cancel_event):
        delays.append(delay)

    monkeypatch.setattr(provider, "sleep_or_cancel", no_wait)
    assert await provider.request_with_retry(operation, timeout=20) == "ok"
    assert len(timeouts) == 3
    assert all(0 < timeout <= provider.MAX_SINGLE_ATTEMPT_SECONDS for timeout in timeouts)
    assert delays == [0.5, 1.0]


@pytest.mark.asyncio
async def test_provider_request_cancels_while_a_non_stream_operation_is_pending():
    cancelled = asyncio.Event()
    started = asyncio.Event()

    async def operation(_timeout: float):
        started.set()
        await asyncio.Future()

    task = asyncio.create_task(
        provider.request_with_retry(operation, timeout=10, cancel_event=cancelled)
    )
    await started.wait()
    cancelled.set()
    with pytest.raises(asyncio.CancelledError):
        await task


