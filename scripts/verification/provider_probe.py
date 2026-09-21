"""Live capability probe for the configured DeepSeek and embedding APIs."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel

from offerpilot.core.config import settings


class _JsonProbeResponse(BaseModel):
    ok: bool


@dataclass
class ProbeResult:
    name: str
    passed: bool
    detail: str = ""
    http_status: int | None = None
    metadata: dict[str, Any] | None = None


_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "probe_echo",
        "description": "Echo the supplied probe value.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    },
}


def _safe_error(exc: Exception) -> tuple[str, int | None]:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    name = exc.__class__.__name__.lower()
    if status in {401, 403}:
        return "authentication_failed", status
    if status == 429:
        return "rate_limited", status
    if isinstance(status, int) and status >= 500:
        return "provider_server_error", status
    if "timeout" in name:
        return "timeout", status if isinstance(status, int) else None
    if "connection" in name or "network" in name:
        return "network_error", status if isinstance(status, int) else None
    return "provider_request_failed", status if isinstance(status, int) else None


def _text_client():
    from openai import OpenAI

    return OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=20.0,
        max_retries=0,
    )


def _embedding_client():
    from openai import OpenAI

    return OpenAI(
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        timeout=settings.embedding_timeout_seconds,
        max_retries=0,
    )


def _probe_forced_tool_call(client: Any) -> ProbeResult:
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": "Call probe_echo exactly once with value ok."}],
            tools=[_PROBE_TOOL],
            tool_choice="required",
            temperature=0,
            max_tokens=32,
        )
        calls = response.choices[0].message.tool_calls or []
        if len(calls) != 1 or not calls[0].id or calls[0].function.name != "probe_echo":
            return ProbeResult("forced_tool_call", False, "missing_standard_tool_call")
        arguments = json.loads(calls[0].function.arguments)
        return ProbeResult(
            "forced_tool_call",
            isinstance(arguments, dict) and arguments.get("value") == "ok",
            metadata={"tool_call_count": len(calls), "has_tool_call_id": bool(calls[0].id)},
        )
    except Exception as exc:
        detail, status = _safe_error(exc)
        return ProbeResult("forced_tool_call", False, detail, status)


def _probe_stream_tool_call(client: Any) -> ProbeResult:
    try:
        stream = client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": "Call probe_echo exactly once with value ok."}],
            tools=[_PROBE_TOOL],
            tool_choice="required",
            temperature=0,
            max_tokens=32,
            stream=True,
        )
        calls: dict[int, dict[str, str]] = {}
        for chunk in stream:
            # Some OpenAI-compatible gateways emit a usage-only terminal chunk.
            # It has no choices and is not a malformed tool-call delta.
            for choice in chunk.choices or []:
                for delta in choice.delta.tool_calls or []:
                    index = delta.index if delta.index is not None else 0
                    item = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if delta.id:
                        item["id"] = delta.id
                    if delta.function and delta.function.name:
                        item["name"] = delta.function.name
                    if delta.function and delta.function.arguments:
                        item["arguments"] += delta.function.arguments
        if len(calls) != 1:
            return ProbeResult("stream_tool_call", False, "missing_streamed_tool_call")
        call = next(iter(calls.values()))
        arguments = json.loads(call["arguments"])
        passed = bool(call["id"]) and call["name"] == "probe_echo" and isinstance(arguments, dict) and arguments.get("value") == "ok"
        return ProbeResult(
            "stream_tool_call",
            passed,
            metadata={"tool_call_count": len(calls), "has_tool_call_id": bool(call["id"])},
        )
    except Exception as exc:
        detail, status = _safe_error(exc)
        return ProbeResult("stream_tool_call", False, detail, status)


def _probe_json_response(client: Any) -> ProbeResult:
    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": "Return exactly the JSON object {\"ok\": true}."}],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=16,
        )
        data = _JsonProbeResponse.model_validate_json(response.choices[0].message.content or "")
        return ProbeResult("json_object", data.ok)
    except Exception as exc:
        detail, status = _safe_error(exc)
        return ProbeResult("json_object", False, detail, status)


def _probe_embedding() -> ProbeResult:
    try:
        client = _embedding_client()
        try:
            dimensions: list[int] = []
            vectors: list[list[float]] = []
            for _ in range(2):
                response = client.embeddings.create(
                    model=settings.embedding_model,
                    input="OfferPilot provider capability probe",
                )
                vector = [float(value) for value in response.data[0].embedding]
                vectors.append(vector)
                dimensions.append(len(vector))
        finally:
            client.close()
        passed = (
            all(vectors)
            and all(all(math.isfinite(value) for value in vector) for vector in vectors)
            and len(set(dimensions)) == 1
        )
        return ProbeResult("embedding", passed, metadata={"dimensions": dimensions[0], "samples": len(vectors)})
    except Exception as exc:
        detail, status = _safe_error(exc)
        return ProbeResult("embedding", False, detail, status)


def run_probe() -> list[ProbeResult]:
    """Run every live capability check without exposing sensitive content."""
    missing = []
    for name, value in {
        "text_api_key": settings.openai_api_key,
        "text_base_url": settings.openai_base_url,
        "text_model": settings.openai_model,
        "embedding_api_key": settings.embedding_api_key,
        "embedding_base_url": settings.embedding_base_url,
        "embedding_model": settings.embedding_model,
    }.items():
        if not value.strip():
            missing.append(name)
    if missing:
        return [ProbeResult("configuration", False, "missing_required_configuration", metadata={"missing": missing})]

    client = _text_client()
    try:
        return [
            _probe_forced_tool_call(client),
            _probe_stream_tool_call(client),
            _probe_json_response(client),
            _probe_embedding(),
        ]
    finally:
        client.close()


def main() -> int:
    results = run_probe()
    print(json.dumps({"results": [asdict(result) for result in results]}, ensure_ascii=False))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
