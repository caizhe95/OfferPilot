"""Embedding calls using the shared provider retry policy."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from offerpilot.core.config import settings
from offerpilot.core.deadlines import remaining_seconds
from offerpilot.llm import provider
from offerpilot.database.values import now
from offerpilot.runs.calls import begin_call, finish_call, record_attempt
from offerpilot.runs.pricing import price_fields


@dataclass(frozen=True)
class _EmbeddingResult:
    vector: list[float]
    input_tokens: int | None
    total_tokens: int | None


def _usage_value(usage: Any, name: str) -> int | None:
    value = getattr(usage, name, None) if usage is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def embed_text(
    text: str,
    *,
    timeout: float | None = None,
    cancel_event: asyncio.Event | None = None,
    deadline: float | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    profile_id: str | None = None,
    logical_call_id: str = "embedding:query",
) -> list[float]:
    if provider.is_placeholder_key(settings.embedding_api_key) or not settings.embedding_base_url.strip():
        raise provider.LLMUnavailableError("Embedding service is not configured")

    async def operation(attempt_timeout: float) -> _EmbeddingResult:
        client = provider.create_client(embedding=True, timeout=attempt_timeout)
        try:
            response = await client.embeddings.create(model=settings.embedding_model, input=text)
            vector = [float(value) for value in response.data[0].embedding]
            if not vector:
                raise ValueError("empty embedding")
            usage = getattr(response, "usage", None)
            return _EmbeddingResult(
                vector=vector,
                input_tokens=_usage_value(usage, "prompt_tokens"),
                total_tokens=_usage_value(usage, "total_tokens"),
            )
        finally:
            await client.close()

    effective_timeout = timeout or settings.embedding_timeout_seconds
    if deadline is not None:
        effective_timeout = min(effective_timeout, remaining_seconds(deadline))
    if effective_timeout <= 0:
        raise provider.ProviderRequestError("timeout", "Embedding request exceeded the active run budget")
    started_at = now() if run_id and session_id and profile_id else ""
    if started_at:
        begin_call(run_id=run_id or "", session_id=session_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id, call_type="embedding", provider="embedding", model=settings.embedding_model, operation_name="embed_text")
    attempt_count = 0

    async def on_attempt(event: dict[str, object]) -> None:
        nonlocal attempt_count
        if run_id and profile_id and event["event"] == "started":
            attempt_count += 1
            record_attempt(run_id, profile_id, logical_call_id, attempt_count, max(0, attempt_count - 1))
    try:
        result = await provider.request_with_retry(operation, timeout=effective_timeout, task="embedding", provider="embedding", model=settings.embedding_model, cancel_event=cancel_event, deadline=deadline, on_attempt=on_attempt)
    except asyncio.CancelledError:
        if started_at:
            finish_call(run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id, status="cancelled", started_at=started_at, error_category="cancelled")
        raise
    except Exception as exc:
        if started_at:
            finish_call(run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id, status="failed", started_at=started_at, error_category=getattr(exc, "category", "provider_request"))
        raise
    if started_at:
        finish_call(
            run_id=run_id or "", profile_id=profile_id or "", logical_call_id=logical_call_id,
            status="succeeded", started_at=started_at, input_tokens=result.input_tokens,
            total_tokens=result.total_tokens, **price_fields("embedding", settings.embedding_model),
        )
    return result.vector
