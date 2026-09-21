"""Validated structured JSON completions for formal diagnosis workflows."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Awaitable, Callable

from offerpilot.core.config import settings
from offerpilot.core.errors import AppError
from offerpilot.core.logging import log_event
from offerpilot.llm import provider
from offerpilot.runs.calls import begin_call, finish_call, record_attempt
from offerpilot.runs.pricing import price_fields
from offerpilot.database.values import now

logger = logging.getLogger(__name__)

MAX_STRUCTURED_OUTPUT_TOKENS = 1200
MAX_STRUCTURED_ATTEMPT_SECONDS = provider.MAX_SINGLE_ATTEMPT_SECONDS
STRUCTURED_PROGRESS_INTERVAL_SECONDS = 5.0


@dataclass
class StructuredLLMResult:
    data: dict[str, Any]
    source: str
    raw_text: str = ""
    error: str = ""
    stream_mode: str = ""
    duration_ms: int = 0
    first_token_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    finish_reason: str | None = None
    generated_chars: int = 0
    validation_duration_ms: int = 0


@dataclass
class _ProviderResponse:
    raw_text: str
    stream_mode: str
    first_token_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    reasoning_tokens: int | None
    finish_reason: str | None
    generated_chars: int


class LLMInvalidResponseError(AppError):
    def __init__(self, message: str = "LLM response is invalid", *, metrics: dict[str, Any] | None = None, validation_duration_ms: int | None = None):
        self.metrics = metrics or {}
        self.validation_duration_ms = validation_duration_ms
        super().__init__(message, code="llm_invalid_response", status_code=503)


class LLMOutputTruncatedError(AppError):
    def __init__(self, *, metrics: dict[str, Any] | None = None):
        self.metrics = metrics or {}
        self.validation_duration_ms = 0
        super().__init__("LLM structured output was truncated", code="llm_output_truncated", status_code=503)


def structured_result_metrics(result: StructuredLLMResult | object) -> dict[str, Any]:
    return {
        "stream_mode": getattr(result, "stream_mode", "") or "unknown",
        "duration_ms": int(getattr(result, "duration_ms", 0) or 0),
        "first_token_ms": getattr(result, "first_token_ms", None),
        "input_tokens": getattr(result, "input_tokens", None),
        "output_tokens": getattr(result, "output_tokens", None),
        "total_tokens": getattr(result, "total_tokens", None),
        "reasoning_tokens": getattr(result, "reasoning_tokens", None),
        "finish_reason": getattr(result, "finish_reason", None),
        "generated_chars": int(getattr(result, "generated_chars", 0) or 0),
    }


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
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


def _value(source: object, name: str) -> object:
    return source.get(name) if isinstance(source, dict) else getattr(source, name, None)


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _usage_values(usage: object) -> tuple[int | None, int | None, int | None, int | None]:
    if usage is None:
        return None, None, None, None
    details = _value(usage, "completion_tokens_details")
    return (
        _optional_int(_value(usage, "prompt_tokens")),
        _optional_int(_value(usage, "completion_tokens")),
        _optional_int(_value(usage, "total_tokens")),
        _optional_int(_value(details, "reasoning_tokens")) if details is not None else None,
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
    on_progress: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    on_fallback: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    profile_id: str | None = None,
    logical_call_id: str | None = None,
) -> StructuredLLMResult:
    if provider.is_placeholder_key(settings.openai_api_key):
        raise provider.LLMUnavailableError()
    selected_model = model or settings.openai_model
    request_started = perf_counter()
    request_deadline = min(deadline if deadline is not None else float("inf"), asyncio.get_running_loop().time() + max(0.001, timeout))
    request_params: dict[str, Any] = {
        "model": selected_model,
        "temperature": temperature,
        "max_tokens": MAX_STRUCTURED_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
        ],
    }
    call_started_at = now() if run_id and session_id and profile_id and logical_call_id else ""
    if call_started_at:
        begin_call(
            run_id=run_id or "", session_id=session_id or "", profile_id=profile_id or "",
            logical_call_id=logical_call_id or task_name, call_type="llm", provider="deepseek",
            model=selected_model, operation_name=task_name,
        )

    attempt_count = 0

    async def on_attempt(event: dict[str, Any]) -> None:
        nonlocal attempt_count
        if run_id and profile_id and logical_call_id and event.get("event") == "started":
            attempt_count += 1
            record_attempt(run_id, profile_id, logical_call_id, attempt_count, max(0, attempt_count - 1))

    async def non_stream(attempt_timeout: float, stream_mode: str) -> _ProviderResponse:
        client = provider.create_client(timeout=attempt_timeout)
        try:
            response = await client.chat.completions.create(**request_params)
            choice = response.choices[0]
            raw_text = choice.message.content or ""
            input_tokens, output_tokens, total_tokens, reasoning_tokens = _usage_values(getattr(response, "usage", None))
            return _ProviderResponse(raw_text, stream_mode, None, input_tokens, output_tokens, total_tokens, reasoning_tokens, str(choice.finish_reason) if choice.finish_reason is not None else None, len(raw_text))
        finally:
            await client.close()

    async def stream(attempt_timeout: float) -> _ProviderResponse:
        client = provider.create_client(timeout=attempt_timeout)
        content_parts: list[str] = []
        first_token_ms: int | None = None
        finish_reason: str | None = None
        usage: object = None
        last_progress_at = request_started
        try:
            try:
                response = await client.chat.completions.create(stream=True, **request_params)
                iterator = response.__aiter__()
                while True:
                    try:
                        chunk = await provider.next_stream_chunk(iterator, cancel_event)
                    except StopAsyncIteration:
                        break
                    if getattr(chunk, "usage", None) is not None:
                        usage = chunk.usage
                    for choice in chunk.choices or []:
                        if choice.finish_reason is not None:
                            finish_reason = str(choice.finish_reason)
                        content = getattr(choice.delta, "content", None)
                        if not content:
                            continue
                        if first_token_ms is None:
                            first_token_ms = max(0, int(round((perf_counter() - request_started) * 1000)))
                        content_parts.append(str(content))
                        current = perf_counter()
                        if current - last_progress_at >= STRUCTURED_PROGRESS_INTERVAL_SECONDS:
                            await provider.invoke_callback(on_progress, {"duration_ms": max(0, int(round((current - request_started) * 1000))), "generated_chars": sum(len(part) for part in content_parts)})
                            last_progress_at = current
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not content_parts and provider.is_streaming_structured_response_unsupported(exc):
                    raise provider.StreamingStructuredResponseUnsupportedError() from exc
                raise
            raw_text = "".join(content_parts)
            input_tokens, output_tokens, total_tokens, reasoning_tokens = _usage_values(usage)
            return _ProviderResponse(raw_text, "stream", first_token_ms, input_tokens, output_tokens, total_tokens, reasoning_tokens, finish_reason, len(raw_text))
        finally:
            await client.close()

    try:
        try:
            result = await provider.request_with_retry(
                stream,
                timeout=timeout,
                task=task_name,
                provider="deepseek",
                model=selected_model,
                max_attempt_seconds=MAX_STRUCTURED_ATTEMPT_SECONDS,
                cancel_event=cancel_event,
                deadline=request_deadline,
                on_attempt=on_attempt,
            )
        except provider.ProviderRequestError as exc:
            if exc.category != "stream_structured_unsupported":
                raise
            await provider.invoke_callback(
                on_fallback,
                {
                    "reason": "stream_json_unsupported",
                    "stream_mode": "non_stream_fallback",
                },
            )
            result = await provider.request_with_retry(
                lambda attempt_timeout: non_stream(attempt_timeout, "non_stream_fallback"),
                timeout=max(0.001, request_deadline - asyncio.get_running_loop().time()),
                task=task_name,
                provider="deepseek",
                model=selected_model,
                max_attempt_seconds=MAX_STRUCTURED_ATTEMPT_SECONDS,
                cancel_event=cancel_event,
                deadline=request_deadline,
                on_attempt=on_attempt,
            )
    except asyncio.CancelledError:
        if call_started_at:
            finish_call(run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id or task_name, status="cancelled", started_at=call_started_at, error_category="cancelled")
        raise
    except AppError:
        if call_started_at:
            finish_call(run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id or task_name, status="failed", started_at=call_started_at, error_category="provider")
        raise
    except Exception as exc:
        if call_started_at:
            finish_call(run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id or task_name, status="failed", started_at=call_started_at, error_category="provider")
        raise provider.LLMUnavailableError(f"{task_name}: LLM request failed") from exc

    completed = StructuredLLMResult(
        data={}, source="llm", raw_text=result.raw_text, stream_mode=result.stream_mode,
        duration_ms=max(0, int(round((perf_counter() - request_started) * 1000))),
        first_token_ms=result.first_token_ms, input_tokens=result.input_tokens,
        output_tokens=result.output_tokens, total_tokens=result.total_tokens,
        reasoning_tokens=result.reasoning_tokens, finish_reason=result.finish_reason,
        generated_chars=result.generated_chars,
    )
    metrics = structured_result_metrics(completed)
    log_event(logger, logging.INFO, "structured_model_completed", task=task_name, provider="deepseek", model=selected_model, **metrics)
    if completed.finish_reason == "length":
        if call_started_at:
            finish_call(
                run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id or task_name,
                status="failed", started_at=call_started_at, input_tokens=completed.input_tokens,
                output_tokens=completed.output_tokens, total_tokens=completed.total_tokens,
                reasoning_tokens=completed.reasoning_tokens, first_token_ms=completed.first_token_ms,
                error_category="output_truncated", **price_fields("deepseek", selected_model),
            )
        raise LLMOutputTruncatedError(metrics=metrics)
    validation_started = perf_counter()
    try:
        if not completed.raw_text.strip():
            raise ValueError("empty model response")
        data = _parse_json_object(completed.raw_text)
        if validator and not validator(data):
            raise ValueError("structured response failed validation")
        completed.data = data
        completed.validation_duration_ms = max(0, int(round((perf_counter() - validation_started) * 1000)))
        if call_started_at:
            finish_call(
                run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id or task_name,
                status="succeeded", started_at=call_started_at, input_tokens=completed.input_tokens,
                output_tokens=completed.output_tokens, total_tokens=completed.total_tokens,
                reasoning_tokens=completed.reasoning_tokens, first_token_ms=completed.first_token_ms,
                **price_fields("deepseek", selected_model),
            )
        return completed
    except Exception as exc:
        if call_started_at:
            finish_call(
                run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id or task_name,
                status="failed", started_at=call_started_at, input_tokens=completed.input_tokens,
                output_tokens=completed.output_tokens, total_tokens=completed.total_tokens,
                reasoning_tokens=completed.reasoning_tokens, first_token_ms=completed.first_token_ms,
                error_category="invalid_response",
                **price_fields("deepseek", selected_model),
            )
        raise LLMInvalidResponseError(
            f"{task_name}: invalid structured response",
            metrics=metrics,
            validation_duration_ms=max(0, int(round((perf_counter() - validation_started) * 1000))),
        ) from exc
