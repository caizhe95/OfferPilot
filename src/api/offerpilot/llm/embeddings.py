"""Embedding calls using the shared provider retry policy."""

from __future__ import annotations

import asyncio

from offerpilot.core.config import settings
from offerpilot.core.deadlines import remaining_seconds
from offerpilot.llm import provider


async def embed_text(
    text: str,
    *,
    timeout: float | None = None,
    cancel_event: asyncio.Event | None = None,
    deadline: float | None = None,
) -> list[float]:
    if provider.is_placeholder_key(settings.embedding_api_key) or not settings.embedding_base_url.strip():
        raise provider.LLMUnavailableError("Embedding service is not configured")

    async def operation(attempt_timeout: float) -> list[float]:
        client = provider.create_client(embedding=True, timeout=attempt_timeout)
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
        raise provider.ProviderRequestError("timeout", "Embedding request exceeded the active run budget")
    return await provider.request_with_retry(
        operation,
        timeout=effective_timeout,
        task="embedding",
        provider="embedding",
        model=settings.embedding_model,
        cancel_event=cancel_event,
        deadline=deadline,
    )
