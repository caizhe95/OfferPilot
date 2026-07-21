"""Structured LLM helper for semantic decisions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, cast

from app.core.config import settings
from app.core.errors import AppError


@dataclass
class StructuredLLMResult:
    """Result from a structured LLM request."""

    data: dict[str, Any]
    source: str
    raw_text: str = ""
    error: str = ""


@dataclass
class ToolChatResult:
    """Unstructured assistant response used by the Coach tool loop."""

    content: str
    tool_calls: list[dict[str, Any]]


class LLMUnavailableError(AppError):
    """Raised when the configured LLM service cannot be used."""

    def __init__(self, message: str = "LLM service is unavailable or not configured"):
        super().__init__(message, code="llm_unavailable", status_code=503)


class LLMInvalidResponseError(AppError):
    """Raised when the LLM returns malformed or schema-invalid JSON."""

    def __init__(self, message: str = "LLM response is invalid"):
        super().__init__(message, code="llm_invalid_response", status_code=503)


def _is_placeholder_key(value: str) -> bool:
    key = (value or "").strip()
    if not key:
        return True
    lowered = key.lower()
    return lowered in {"sk-xxx", "sk-...", "your-api-key", "<your-api-key>"}


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

    if _is_placeholder_key(settings.openai_api_key):
        raise LLMUnavailableError()

    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=timeout,
        max_retries=0,
    )

    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            # Some OpenAI-compatible relays corrupt non-ASCII JSON request bodies.
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
        ],
    )
    return response.choices[0].message.content or ""


def tool_chat_completion(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: str | dict[str, Any] = "auto",
    timeout: float = 12.0,
    model: str | None = None,
) -> ToolChatResult:
    """Call the OpenAI-compatible native Function Calling API exactly once."""
    if _is_placeholder_key(settings.openai_api_key):
        raise LLMUnavailableError()
    try:
        from openai import OpenAI
        from openai.types.chat import (
            ChatCompletionMessageFunctionToolCall,
            ChatCompletionMessageParam,
            ChatCompletionToolChoiceOptionParam,
            ChatCompletionToolParam,
        )

        client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=max(0.1, timeout),
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=model or settings.openai_model,
            messages=cast(list[ChatCompletionMessageParam], messages),
            tools=cast(list[ChatCompletionToolParam], tools),
            tool_choice=cast(ChatCompletionToolChoiceOptionParam, tool_choice),
            temperature=0.2,
        )
        message = response.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            if not isinstance(call, ChatCompletionMessageFunctionToolCall):
                raise LLMInvalidResponseError("coach returned an unsupported non-function tool call")
            calls.append({
                "id": call.id,
                "name": call.function.name,
                "arguments": call.function.arguments,
            })
        return ToolChatResult(content=message.content or "", tool_calls=calls)
    except AppError:
        raise
    except Exception as exc:
        raise LLMUnavailableError(f"coach_tool_chat: LLM request failed: {exc}") from exc


def structured_json_completion(
    *,
    task_name: str,
    system_prompt: str,
    user_payload: dict[str, Any],
    validator: Callable[[dict[str, Any]], bool] | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float = 12.0,
) -> StructuredLLMResult:
    """Request a structured JSON object from the model."""
    try:
        raw_text = _call_openai_chat_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
            model=model or settings.openai_model,
            temperature=temperature,
            timeout=timeout,
        )
    except AppError:
        raise
    except Exception as exc:
        if "timeout" in exc.__class__.__name__.lower():
            raise LLMUnavailableError(f"{task_name}: LLM request timed out") from exc
        raise LLMUnavailableError(f"{task_name}: LLM request failed") from exc

    try:
        if not raw_text.strip():
            raise ValueError(f"{task_name}: empty model response")
        data = _parse_json_object(raw_text)
        if validator is not None and not validator(data):
            raise ValueError(f"{task_name}: structured response failed validation")
        return StructuredLLMResult(data=data, source="llm", raw_text=raw_text)
    except Exception as exc:
        raise LLMInvalidResponseError(f"{task_name}: invalid structured response") from exc
