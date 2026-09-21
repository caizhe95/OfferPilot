"""Official DeepSeek provider plumbing and stable failure mapping."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime
from email.utils import parsedate_to_datetime
from time import perf_counter
from typing import Any, Awaitable, Callable

from offerpilot.core.config import settings
from offerpilot.core.errors import AppError
from offerpilot.core.logging import log_event

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAYS = (0.5, 1.0)
MAX_SINGLE_ATTEMPT_SECONDS = 12.0


class LLMUnavailableError(AppError):
    def __init__(self, message: str = "LLM service is unavailable or not configured"):
        super().__init__(message, code="llm_unavailable", status_code=503)


class ProviderRequestError(LLMUnavailableError):
    """A provider failure with a stable, non-sensitive category."""

    def __init__(self, category: str, message: str = "Provider request failed"):
        super().__init__(message)
        self.category = category


def is_placeholder_key(value: str) -> bool:
    key = (value or "").strip()
    return not key or key.lower() in {"sk-xxx", "sk-...", "your-api-key", "<your-api-key>"}


def classify_provider_error(exc: BaseException) -> tuple[str, bool, float | None]:
    """Return a non-sensitive category, retryability, and optional retry delay."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    retry_after = headers.get("retry-after") if hasattr(headers, "get") else None
    retry_after_seconds = parse_retry_after(retry_after)
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


def parse_retry_after(value: object) -> float | None:
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


async def sleep_or_cancel(delay: float, cancel_event: asyncio.Event | None) -> None:
    if cancel_event is None:
        await asyncio.sleep(delay)
        return
    try:
        await asyncio.wait_for(cancel_event.wait(), timeout=delay)
    except TimeoutError:
        return
    raise asyncio.CancelledError()


async def await_attempt(
    operation: Awaitable[Any],
    *,
    timeout: float,
    cancel_event: asyncio.Event | None,
) -> Any:
    """Await one provider operation while reacting to Run cancellation."""
    task = asyncio.ensure_future(operation)
    cancel_task: asyncio.Task[bool] | None = None
    try:
        if cancel_event is None:
            return await asyncio.wait_for(task, timeout=timeout)
        cancel_task = asyncio.create_task(cancel_event.wait())
        done, _ = await asyncio.wait(
            {task, cancel_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if cancel_task in done:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise asyncio.CancelledError()
        if task in done:
            return task.result()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        raise TimeoutError()
    finally:
        if cancel_task is not None:
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task


async def next_stream_chunk(iterator: Any, cancel_event: asyncio.Event | None) -> Any:
    if cancel_event is None:
        return await iterator.__anext__()
    if cancel_event.is_set():
        raise asyncio.CancelledError()
    next_chunk = asyncio.create_task(iterator.__anext__())
    cancelled = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait({next_chunk, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        if cancelled in done and cancel_event.is_set():
            next_chunk.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await next_chunk
            raise asyncio.CancelledError()
        return next_chunk.result()
    finally:
        cancelled.cancel()
        with suppress(asyncio.CancelledError):
            await cancelled


async def invoke_callback(
    callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    result = callback(payload)
    if result is not None:
        await result


async def request_with_retry(
    operation: Callable[[float], Awaitable[Any]],
    *,
    timeout: float,
    task: str = "provider_request",
    provider: str = "text",
    model: str = "",
    max_attempt_seconds: float = MAX_SINGLE_ATTEMPT_SECONDS,
    cancel_event: asyncio.Event | None = None,
    deadline: float | None = None,
) -> Any:
    started = perf_counter()
    request_deadline = min(
        deadline if deadline is not None else float("inf"),
        asyncio.get_running_loop().time() + max(0.001, timeout),
    )
    for attempt in range(MAX_ATTEMPTS):
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError()
        remaining = request_deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise ProviderRequestError("timeout", "Provider request exceeded the active run budget")
        try:
            attempt_timeout = min(max(0.001, max_attempt_seconds), remaining)
            result = await await_attempt(
                operation(attempt_timeout),
                timeout=attempt_timeout,
                cancel_event=cancel_event,
            )
            log_event(logger, logging.INFO, "provider_call_completed", task=task, provider=provider, model=model, attempt=attempt + 1, duration_ms=round((perf_counter() - started) * 1000, 1), result_code="ok")
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            category, retryable, retry_after = classify_provider_error(exc)
            if not retryable or attempt + 1 >= MAX_ATTEMPTS:
                log_event(logger, logging.ERROR, "provider_call_failed", task=task, provider=provider, model=model, attempt=attempt + 1, duration_ms=round((perf_counter() - started) * 1000, 1), error_code=category)
                raise ProviderRequestError(category) from None
            delay = retry_after if retry_after is not None else RETRY_DELAYS[attempt]
            if delay >= request_deadline - asyncio.get_running_loop().time():
                log_event(logger, logging.ERROR, "provider_call_failed", task=task, provider=provider, model=model, attempt=attempt + 1, duration_ms=round((perf_counter() - started) * 1000, 1), error_code=category)
                raise ProviderRequestError(category, "Provider retry would exceed the active run budget") from None
            log_event(logger, logging.WARNING, "provider_retry", task=task, provider=provider, model=model, attempt=attempt + 1, retry_in_ms=round(delay * 1000), error_code=category)
            await sleep_or_cancel(max(0.0, delay), cancel_event)
    raise ProviderRequestError("provider_request")


def create_client(*, embedding: bool = False, timeout: float):
    from openai import AsyncOpenAI

    api_key = settings.embedding_api_key if embedding else settings.openai_api_key
    base_url = settings.embedding_base_url if embedding else settings.openai_base_url
    return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
