"""Structured LLM helper for semantic decisions.

This module provides a small compatibility layer around OpenAI-compatible
chat completions. It keeps all semantic call sites on the same contract:
attempt a structured JSON response, validate it, and fall back cleanly when
the model is unavailable or returns malformed output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from app.core.config import settings


@dataclass
class StructuredLLMResult:
    """Result from a structured LLM request."""

    data: dict[str, Any]
    source: str
    raw_text: str = ""
    error: str = ""


def should_use_mock_llm() -> bool:
    """Return True when the environment should not call a real model."""
    if settings.mock_agent:
        return True
    if not settings.openai_api_key:
        return True
    if settings.openai_api_key == "sk-xxx":
        return True
    return False


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
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(cleaned[start : end + 1])
        else:
            raise

    if not isinstance(parsed, dict):
        raise ValueError("Structured response must be a JSON object")
    return parsed


def _call_openai_chat_json(
    *,
    system_prompt: str,
    user_payload: dict[str, Any],
    model: str,
    temperature: float,
    timeout: float,
) -> str:
    from openai import OpenAI

    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=timeout,
    )

    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    )
    return response.choices[0].message.content or ""


def structured_json_completion(
    *,
    task_name: str,
    system_prompt: str,
    user_payload: dict[str, Any],
    fallback_factory: Callable[[], dict[str, Any]],
    validator: Callable[[dict[str, Any]], bool] | None = None,
    mock_factory: Callable[[], dict[str, Any]] | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float = 12.0,
) -> StructuredLLMResult:
    """Request a structured JSON object from the model.

    The return value never raises on model failure; invalid or unavailable
    model output is replaced by the fallback payload.
    """
    fallback_payload = fallback_factory
    if should_use_mock_llm():
        data = (mock_factory or fallback_factory)()
        return StructuredLLMResult(data=data, source="mock")

    try:
        raw_text = _call_openai_chat_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
            model=model or settings.openai_model,
            temperature=temperature,
            timeout=timeout,
        )
        if not raw_text.strip():
            raise ValueError(f"{task_name}: empty model response")
        data = _parse_json_object(raw_text)
        if validator is not None and not validator(data):
            raise ValueError(f"{task_name}: structured response failed validation")
        return StructuredLLMResult(data=data, source="llm", raw_text=raw_text)
    except Exception as exc:
        data = fallback_payload()
        return StructuredLLMResult(data=data, source="fallback", error=str(exc))
