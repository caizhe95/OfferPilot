"""Coach completion using the official DeepSeek Chat Completions API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from offerpilot.core.config import settings
from offerpilot.llm import provider

MAX_TOOL_ATTEMPT_SECONDS = provider.MAX_SINGLE_ATTEMPT_SECONDS
MAX_TOOL_CALL_TOKENS = 256
MAX_CHAT_OUTPUT_TOKENS = 512


@dataclass
class ToolChatResult:
    content: str
    tool_calls: list[dict[str, Any]]


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
) -> ToolChatResult:
    if provider.is_placeholder_key(settings.openai_api_key):
        raise provider.LLMUnavailableError()

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
            return ToolChatResult(content=content, tool_calls=calls)
        finally:
            await client.close()

    return await provider.request_with_retry(
        operation,
        timeout=timeout,
        task="coach_tool_chat",
        provider="deepseek",
        model=model or settings.openai_model,
        max_attempt_seconds=MAX_TOOL_ATTEMPT_SECONDS,
        cancel_event=cancel_event,
        deadline=deadline,
    )
