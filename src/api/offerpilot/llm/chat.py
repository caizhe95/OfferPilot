"""Coach completion using the official DeepSeek Chat Completions API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from offerpilot.core.config import settings
from offerpilot.llm import provider
from offerpilot.database.values import now
from offerpilot.runs.calls import begin_call, finish_call, record_attempt
from offerpilot.runs.pricing import price_fields

MAX_TOOL_ATTEMPT_SECONDS = provider.MAX_SINGLE_ATTEMPT_SECONDS
MAX_TOOL_CALL_TOKENS = 256
MAX_CHAT_OUTPUT_TOKENS = 512


@dataclass
class ToolChatResult:
    content: str
    tool_calls: list[dict[str, Any]]
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None


async def tool_chat_completion(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: str | dict[str, Any] = "auto",
    timeout: float = 12.0,
    model: str | None = None,
    on_content_delta: Callable[[str], Awaitable[None] | None] | None = None,
    cancel_event: asyncio.Event | None = None,
    deadline: float | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    profile_id: str | None = None,
    logical_call_id: str = "coach:chat",
) -> ToolChatResult:
    if provider.is_placeholder_key(settings.openai_api_key):
        raise provider.LLMUnavailableError()

    started_at = now() if run_id and session_id and profile_id else ""
    if started_at:
        begin_call(run_id=run_id or "", session_id=session_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id, call_type="llm", provider="deepseek", model=model or settings.openai_model, operation_name="tool_chat_completion")

    def usage_values(usage: Any) -> tuple[int | None, int | None, int | None, int | None]:
        if usage is None:
            return None, None, None, None
        details = getattr(usage, "completion_tokens_details", None)
        return (
            getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None),
            getattr(usage, "total_tokens", None), getattr(details, "reasoning_tokens", None) if details else None,
        )

    async def operation(attempt_timeout: float) -> ToolChatResult:
        client = provider.create_client(timeout=attempt_timeout)
        request_params: dict[str, Any] = {
            "model": model or settings.openai_model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": MAX_TOOL_CALL_TOKENS if tools else MAX_CHAT_OUTPUT_TOKENS,
        }
        if tools:
            request_params["tools"] = tools
            request_params["tool_choice"] = tool_choice
        try:
            response = await client.chat.completions.create(**request_params)
            message = response.choices[0].message
            content = str(message.content or "")
            if content and on_content_delta:
                emitted = on_content_delta(content)
                if asyncio.iscoroutine(emitted):
                    await emitted
            calls = [
                {
                    "id": str(call.id or ""),
                    "name": str(call.function.name or ""),
                    "arguments": str(call.function.arguments or "{}"),
                }
                for call in (getattr(message, "tool_calls", None) or [])
            ]
            input_tokens, output_tokens, total_tokens, reasoning_tokens = usage_values(getattr(response, "usage", None))
            return ToolChatResult(content=content, tool_calls=calls, input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens, reasoning_tokens=reasoning_tokens)
        finally:
            await client.close()

    attempt_count = 0

    async def on_attempt(event: dict[str, Any]) -> None:
        nonlocal attempt_count
        if run_id and profile_id and event["event"] == "started":
            attempt_count += 1
            record_attempt(run_id, profile_id, logical_call_id, attempt_count, max(0, attempt_count - 1))
    try:
        result = await provider.request_with_retry(
            operation, timeout=timeout, task="coach_tool_chat", provider="deepseek", model=model or settings.openai_model,
            max_attempt_seconds=MAX_TOOL_ATTEMPT_SECONDS, cancel_event=cancel_event, deadline=deadline, on_attempt=on_attempt,
        )
    except asyncio.CancelledError:
        if started_at:
            finish_call(run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id, status="cancelled", started_at=started_at, error_category="cancelled")
        raise
    except Exception as exc:
        if started_at:
            finish_call(run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id, status="failed", started_at=started_at, error_category=getattr(exc, "category", "provider_request"))
        raise
    if started_at:
        finish_call(run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id, status="succeeded", started_at=started_at, input_tokens=result.input_tokens, output_tokens=result.output_tokens, total_tokens=result.total_tokens, reasoning_tokens=result.reasoning_tokens, **price_fields("deepseek", model or settings.openai_model))
    return result
