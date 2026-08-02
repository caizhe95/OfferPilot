"""Tests for the strict structured LLM adapter."""

import asyncio
from types import SimpleNamespace

import pytest

from offerpilot.core.config import settings
from offerpilot.llm.llm_client import (
    ProviderRequestError,
    _VisibleStreamError,
    _request_with_retry,
    structured_json_completion,
    tool_chat_completion,
    LLMUnavailableError,
)


@pytest.mark.asyncio
async def test_structured_completion_requires_configured_key(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")

    with pytest.raises(LLMUnavailableError):
        await structured_json_completion(
            task_name="unit_missing_key",
            system_prompt="Return JSON only.",
            user_payload={"input": "x"},
            validator=lambda data: True,
        )


@pytest.mark.asyncio
async def test_structured_completion_accepts_real_json(monkeypatch):
    async def fake_request_with_retry(operation, **kwargs):
        return '{"ok": true}'

    import offerpilot.llm.llm_client as llm_client_module
    monkeypatch.setattr(llm_client_module, "_request_with_retry", fake_request_with_retry)

    result = await structured_json_completion(
        task_name="unit_real_json",
        system_prompt="只输出 JSON：{\"ok\": true}。",
        user_payload={"input": "请返回 ok=true"},
        validator=lambda data: data.get("ok") is True,
        temperature=0.0,
    )

    assert result.source == "llm"
    assert result.data["ok"] is True


@pytest.mark.asyncio
async def test_provider_retries_three_total_attempts_with_fixed_delays(monkeypatch):
    import offerpilot.llm.llm_client as llm_client

    calls: list[float] = []
    delays: list[float] = []

    class ServerError(Exception):
        status_code = 503

    async def operation(timeout: float) -> str:
        calls.append(timeout)
        if len(calls) < 3:
            raise ServerError()
        return "ok"

    async def fake_sleep(delay: float, _cancel_event):
        delays.append(delay)

    monkeypatch.setattr(llm_client, "_sleep_or_cancel", fake_sleep)
    assert await _request_with_retry(operation, timeout=10) == "ok"
    assert len(calls) == 3
    assert all(timeout <= 4.0 for timeout in calls)
    assert delays == [0.5, 1.0]


@pytest.mark.asyncio
async def test_provider_honors_retry_after(monkeypatch):
    import offerpilot.llm.llm_client as llm_client

    delays: list[float] = []
    attempts = 0

    class RateLimited(Exception):
        status_code = 429
        response = SimpleNamespace(headers={"retry-after": "0.25"})

    async def operation(_timeout: float) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RateLimited()
        return "ok"

    async def fake_sleep(delay: float, _cancel_event):
        delays.append(delay)

    monkeypatch.setattr(llm_client, "_sleep_or_cancel", fake_sleep)
    assert await _request_with_retry(operation, timeout=10) == "ok"
    assert delays == [0.25]


@pytest.mark.asyncio
async def test_visible_stream_failure_is_not_retried(monkeypatch):
    import offerpilot.llm.llm_client as llm_client

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    clients: list[object] = []

    class Stream:
        def __init__(self):
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._sent:
                self._sent = True
                return SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="visible", tool_calls=[]))]
                )
            raise RuntimeError("connection lost")

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        async def create(self, **_kwargs):
            return Stream()

        async def close(self):
            return None

    def fake_client(**_kwargs):
        client = Client()
        clients.append(client)
        return client

    emitted: list[str] = []
    monkeypatch.setattr(llm_client, "_client", fake_client)
    with pytest.raises(ProviderRequestError) as exc_info:
        await tool_chat_completion(
            messages=[{"role": "user", "content": "test"}],
            tools=[],
            on_content_delta=emitted.append,
            timeout=10,
        )
    assert exc_info.value.category == "stream_interrupted"
    assert emitted == ["visible"]
    assert len(clients) == 1


@pytest.mark.asyncio
async def test_stream_timeout_before_visible_output_retries_as_async_non_streaming_completion(monkeypatch):
    import offerpilot.llm.llm_client as llm_client

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    request_modes: list[bool] = []

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            streaming = bool(kwargs.get("stream"))
            request_modes.append(streaming)
            if streaming:
                raise TimeoutError()
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="done",
                            tool_calls=[
                                SimpleNamespace(
                                    id="call_1",
                                    function=SimpleNamespace(name="search_knowledge", arguments='{"question":"ReAct"}'),
                                )
                            ],
                        )
                    )
                ]
            )

        async def close(self):
            return None

    monkeypatch.setattr(llm_client, "_client", lambda **_kwargs: Client())

    async def no_sleep(_delay: float, _cancel_event):
        return None

    monkeypatch.setattr(llm_client, "_sleep_or_cancel", no_sleep)
    result = await tool_chat_completion(
        messages=[{"role": "user", "content": "search"}],
        tools=[],
        timeout=10,
    )

    assert request_modes == [True, False]
    assert result.content == "done"
    assert result.tool_calls == [{"id": "call_1", "name": "search_knowledge", "arguments": '{"question":"ReAct"}'}]


@pytest.mark.asyncio
async def test_non_streaming_completion_never_attempts_a_visible_stream(monkeypatch):
    import offerpilot.llm.llm_client as llm_client

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    request_modes: list[bool] = []
    request_limits: list[int] = []

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            request_modes.append(bool(kwargs.get("stream")))
            request_limits.append(int(kwargs["max_tokens"]))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=[]))])

        async def close(self):
            return None

    monkeypatch.setattr(llm_client, "_client", lambda **_kwargs: Client())
    result = await tool_chat_completion(
        messages=[{"role": "tool", "content": "tool result", "tool_call_id": "call_1"}],
        tools=[],
        timeout=10,
        stream_response=False,
    )

    assert request_modes == [False]
    assert request_limits == [llm_client.MAX_CHAT_OUTPUT_TOKENS]
    assert result.content == "done"


@pytest.mark.asyncio
async def test_tool_completion_uses_bounded_tool_output_budget(monkeypatch):
    import offerpilot.llm.llm_client as llm_client

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    request_limits: list[int] = []

    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kwargs):
            request_limits.append(int(kwargs["max_tokens"]))
            return Stream()

        async def close(self):
            return None

    monkeypatch.setattr(llm_client, "_client", lambda **_kwargs: Client())
    await tool_chat_completion(
        messages=[{"role": "user", "content": "search"}],
        tools=[{"type": "function", "function": {"name": "search_knowledge", "parameters": {}}}],
        timeout=10,
    )

    assert request_limits == [llm_client.MAX_TOOL_CALL_TOKENS]


@pytest.mark.asyncio
async def test_provider_timeout_respects_single_attempt_and_total_budget():
    attempt_timeouts: list[float] = []

    async def slow_operation(timeout: float) -> str:
        attempt_timeouts.append(timeout)
        await asyncio.sleep(0.05)
        return "late"

    with pytest.raises(ProviderRequestError) as exc_info:
        await _request_with_retry(slow_operation, timeout=0.01)
    assert exc_info.value.category == "timeout"
    assert len(attempt_timeouts) == 1
    assert attempt_timeouts[0] <= 0.1


@pytest.mark.asyncio
async def test_visible_stream_marker_stops_retry_directly():
    async def interrupted(_timeout: float) -> str:
        raise _VisibleStreamError()

    with pytest.raises(ProviderRequestError) as exc_info:
        await _request_with_retry(interrupted, timeout=10)
    assert exc_info.value.category == "stream_interrupted"
