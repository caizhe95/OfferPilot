"""Async OpenAI-compatible provider adapter used by all live model calls."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from offerpilot.core.config import settings
from offerpilot.core.deadline import remaining_seconds
from offerpilot.core.errors import AppError

MAX_ATTEMPTS = 3
RETRY_DELAYS = (0.5, 1.0)
MAX_SINGLE_ATTEMPT_SECONDS = 4.0
MAX_TOOL_CALL_TOKENS = 256
MAX_CHAT_OUTPUT_TOKENS = 512


@dataclass
class StructuredLLMResult:
    data: dict[str, Any]
    source: str
    raw_text: str = ""
    error: str = ""


@dataclass
class ToolChatResult:
    content: str
    tool_calls: list[dict[str, Any]]


class LLMUnavailableError(AppError):
    def __init__(self, message: str = "LLM service is unavailable or not configured"):
        super().__init__(message, code="llm_unavailable", status_code=503)


class LLMInvalidResponseError(AppError):
    def __init__(self, message: str = "LLM response is invalid"):
        super().__init__(message, code="llm_invalid_response", status_code=503)


class ProviderRequestError(LLMUnavailableError):
    """A provider failure with a stable category for logs and SSE clients."""

    def __init__(self, category: str, message: str = "Provider request failed"):
        super().__init__(message)
        self.category = category


class _VisibleStreamError(Exception):
    """A stream failed after client-visible output, so retrying would duplicate text."""


def _is_placeholder_key(value: str) -> bool:
    key = (value or "").strip()
    return not key or key.lower() in {"sk-xxx", "sk-...", "your-api-key", "<your-api-key>"}


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = _strip_code_fences(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Structured response must be a JSON object")
    return parsed


def classify_provider_error(exc: BaseException) -> tuple[str, bool, float | None]:
    """Return a non-sensitive category, retryability and optional retry delay."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    retry_after = headers.get("retry-after") if hasattr(headers, "get") else None
    retry_after_seconds = _parse_retry_after(retry_after)
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    if status in {401, 403}:
        return "authentication", False, None
    if status in {400, 404, 409, 413, 422} or "schema" in text or "context" in text:
        return "request_invalid", False, None
    if status == 429:
        return "rate_limited", True, retry_after_seconds
    if isinstance(status, int) and status >= 500:
        return "provider_server", True, retry_after_seconds
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in name:
        return "timeout", True, retry_after_seconds
    if "connection" in name or "network" in name or "connect" in text:
        return "network", True, retry_after_seconds
    return "provider_request", False, retry_after_seconds


def _parse_retry_after(value: object) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(str(value)))
    except (TypeError, ValueError):
        pass
    try:
        retry_at = parsedate_to_datetime(str(value))
        return max(0.0, (retry_at - datetime.now(retry_at.tzinfo)).total_seconds())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


async def _sleep_or_cancel(delay: float, cancel_event: asyncio.Event | None) -> None:
    if cancel_event is None:
        await asyncio.sleep(delay)
        return
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=delay)
    except TimeoutError:
        return
    raise asyncio.CancelledError()


async def _request_with_retry(
    operation: Callable[[float], Awaitable[Any]],
    *,
    timeout: float,
    cancel_event: asyncio.Event | None = None,
    deadline: float | None = None,
) -> Any:
    request_deadline = min(
        deadline if deadline is not None else float("inf"),
        asyncio.get_running_loop().time() + max(0.001, timeout),
    )
    last_error: BaseException | None = None
    for attempt in range(MAX_ATTEMPTS):
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError()
        remaining = request_deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise ProviderRequestError("timeout", "Provider request exceeded the active run budget")
        try:
            return await asyncio.wait_for(operation(min(MAX_SINGLE_ATTEMPT_SECONDS, remaining)), timeout=min(MAX_SINGLE_ATTEMPT_SECONDS, remaining))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            if isinstance(exc, _VisibleStreamError):
                raise ProviderRequestError("stream_interrupted", "Provider stream ended after visible output") from exc
            category, retryable, retry_after = classify_provider_error(exc)
            if not retryable or attempt + 1 >= MAX_ATTEMPTS:
                raise ProviderRequestError(category) from exc
            delay = retry_after if retry_after is not None else RETRY_DELAYS[attempt]
            if delay >= request_deadline - asyncio.get_running_loop().time():
                raise ProviderRequestError(category, "Provider retry would exceed the active run budget") from exc
            await _sleep_or_cancel(max(0.0, delay), cancel_event)
    raise ProviderRequestError("provider_request") from last_error


def _client(*, embedding: bool = False, timeout: float):
    from openai import AsyncOpenAI

    api_key = settings.embedding_api_key if embedding else settings.openai_api_key
    base_url = settings.embedding_base_url if embedding else settings.openai_base_url
    return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)


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
    stream_response: bool = True,
) -> ToolChatResult:
    """Stream one native tool-call completion while preserving only visible deltas."""
    if _is_placeholder_key(settings.openai_api_key):
        raise LLMUnavailableError()

    visible_output = False
    attempt_number = 0

    async def operation(attempt_timeout: float) -> ToolChatResult:
        nonlocal visible_output, attempt_number
        use_stream = stream_response and attempt_number == 0
        attempt_number += 1
        client = _client(timeout=attempt_timeout)
        content_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        request_params: dict[str, Any] = {
            "model": model or settings.openai_model,
            "messages": messages,
            "temperature": 0.2,
            # The provider otherwise reserves its large default completion
            # budget, which can consume the entire four-second attempt before
            # a forced native tool call is returned.
            "max_tokens": MAX_TOOL_CALL_TOKENS if tools else MAX_CHAT_OUTPUT_TOKENS,
        }
        if tools:
            request_params["tools"] = tools
            request_params["tool_choice"] = tool_choice
        try:
            if not use_stream:
                # The first attempt always streams. A retry before any visible
                # delta may use a regular async completion for gateways whose
                # streaming handshake exceeds the per-attempt budget.
                response = await client.chat.completions.create(
                    **request_params,
                )
                message = response.choices[0].message
                return ToolChatResult(
                    content=message.content or "",
                    tool_calls=[
                        {
                            "id": call.id or "",
                            "name": call.function.name or "",
                            "arguments": call.function.arguments or "{}",
                        }
                        for call in (message.tool_calls or [])
                    ],
                )
            stream = await client.chat.completions.create(
                stream=True,
                **request_params,
            )
            try:
                async for chunk in stream:
                    if cancel_event and cancel_event.is_set():
                        raise asyncio.CancelledError()
                    for choice in chunk.choices or []:
                        delta = choice.delta
                        if delta.content:
                            visible_output = True
                            content_parts.append(delta.content)
                            if on_content_delta:
                                emitted = on_content_delta(delta.content)
                                if inspect.isawaitable(emitted):
                                    await emitted
                        for tool_delta in delta.tool_calls or []:
                            index = tool_delta.index if tool_delta.index is not None else 0
                            item = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            if tool_delta.id:
                                item["id"] = tool_delta.id
                            if tool_delta.function and tool_delta.function.name:
                                item["name"] = tool_delta.function.name
                            if tool_delta.function and tool_delta.function.arguments:
                                item["arguments"] += tool_delta.function.arguments
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if visible_output:
                    raise _VisibleStreamError() from exc
                raise
            return ToolChatResult(content="".join(content_parts), tool_calls=[calls[index] for index in sorted(calls)])
        finally:
            await client.close()

    return await _request_with_retry(
        operation,
        timeout=timeout,
        cancel_event=cancel_event,
        deadline=deadline,
    )


async def structured_json_completion(
    *,
    task_name: str,
    system_prompt: str,
    user_payload: dict[str, Any],
    validator: Callable[[dict[str, Any]], bool] | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float = 12.0,
    cancel_event: asyncio.Event | None = None,
    deadline: float | None = None,
) -> StructuredLLMResult:
    if _is_placeholder_key(settings.openai_api_key):
        raise LLMUnavailableError()

    async def operation(attempt_timeout: float) -> str:
        client = _client(timeout=attempt_timeout)
        try:
            response = await client.chat.completions.create(
                model=model or settings.openai_model,
                temperature=temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
                ],
            )
            return response.choices[0].message.content or ""
        finally:
            await client.close()

    try:
        raw_text = await _request_with_retry(
            operation,
            timeout=timeout,
            cancel_event=cancel_event,
            deadline=deadline,
        )
    except AppError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise LLMUnavailableError(f"{task_name}: LLM request failed") from exc
    try:
        if not raw_text.strip():
            raise ValueError("empty model response")
        data = _parse_json_object(raw_text)
        if validator and not validator(data):
            raise ValueError("structured response failed validation")
        return StructuredLLMResult(data=data, source="llm", raw_text=raw_text)
    except Exception as exc:
        raise LLMInvalidResponseError(f"{task_name}: invalid structured response") from exc


async def embed_text(
    text: str,
    *,
    timeout: float | None = None,
    cancel_event: asyncio.Event | None = None,
    deadline: float | None = None,
) -> list[float]:
    """Create one embedding through the same retry and cancellation policy."""
    if _is_placeholder_key(settings.embedding_api_key) or not settings.embedding_base_url.strip():
        raise LLMUnavailableError("Embedding service is not configured")

    async def operation(attempt_timeout: float) -> list[float]:
        client = _client(embedding=True, timeout=attempt_timeout)
        try:
            response = await client.embeddings.create(model=settings.embedding_model, input=text)
            vector = [float(value) for value in response.data[0].embedding]
            if not vector:
                raise ValueError("empty embedding")
            return vector
        finally:
            await client.close()

    effective_timeout = timeout or settings.embedding_timeout_seconds
    if deadline is not None:
        effective_timeout = min(effective_timeout, remaining_seconds(deadline))
    if effective_timeout <= 0:
        raise ProviderRequestError("timeout", "Embedding request exceeded the active run budget")
    return await _request_with_retry(
        operation,
        timeout=effective_timeout,
        cancel_event=cancel_event,
        deadline=deadline,
    )
