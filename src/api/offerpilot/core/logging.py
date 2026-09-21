"""Small, structured, privacy-safe application logging helpers."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from offerpilot.core.config import settings

_REQUEST_ID = contextvars.ContextVar("offerpilot_request_id", default="")
_RUN_ID = contextvars.ContextVar("offerpilot_run_id", default="")
_SESSION_ID = contextvars.ContextVar("offerpilot_session_id", default="")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SAFE_FIELDS = {
    "active_duration_ms", "approval_id", "approval_wait_ms", "attempt", "count", "decision", "debug",
    "dimensions", "duration_ms", "error_code", "finish_reason", "first_token_ms",
    "flow_kind", "generated_chars", "input_tokens", "knowledge_count", "log_level", "method", "model", "operation", "output_tokens", "provider",
    "queue_duration_ms", "result_code", "retry_in_ms", "risk_level", "route", "run_id", "run_status", "run_type",
    "session_id", "sqlite_error", "status_code", "stream_mode", "task", "timeout_ms", "tool_name",
    "total_duration_ms", "total_tokens", "reasoning_tokens", "upload_id", "request_id", "exception_type", "stack",
}


def request_id_from_header(value: str | None) -> str:
    return value if value and _SAFE_REQUEST_ID.fullmatch(value) else str(uuid.uuid4())


def bind_context(*, request_id: str | None = None, run_id: str | None = None, session_id: str | None = None) -> list[contextvars.Token[str]]:
    tokens: list[contextvars.Token[str]] = []
    if request_id is not None:
        tokens.append(_REQUEST_ID.set(request_id))
    if run_id is not None:
        tokens.append(_RUN_ID.set(run_id))
    if session_id is not None:
        tokens.append(_SESSION_ID.set(session_id))
    return tokens


def reset_context(tokens: list[contextvars.Token[str]]) -> None:
    for token in reversed(tokens):
        token.var.reset(token)


def _safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    result = {
        "request_id": _REQUEST_ID.get(),
        "run_id": _RUN_ID.get(),
        "session_id": _SESSION_ID.get(),
    }
    for key, value in fields.items():
        if key in _SAFE_FIELDS and value is not None:
            result[key] = value
    return {key: value for key, value in result.items() if value != "" and value is not None}


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _safe_fields({}).items():
            if not getattr(record, key, None):
                setattr(record, key, value)
        if not hasattr(record, "event"):
            record.event = record.getMessage()
        return True


class _MaximumLevelFilter(logging.Filter):
    def __init__(self, level: int) -> None:
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.level


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {key: getattr(record, key) for key in _SAFE_FIELDS if getattr(record, key, None) != "" and getattr(record, key, None) is not None}
        details = " ".join(f"{key}={json.dumps(value, ensure_ascii=False, default=str)}" for key, value in sorted(payload.items()))
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds")
        event = getattr(record, "event", "log")
        return f"{timestamp} {record.levelname} {record.name} event={event}" + (f" {details}" if details else "")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }
        payload.update({key: getattr(record, key) for key in _SAFE_FIELDS if getattr(record, key, None) != "" and getattr(record, key, None) is not None})
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging() -> None:
    level_name = settings.log_level.upper()
    if level_name not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise RuntimeError("OFFERPILOT_LOG_LEVEL must be DEBUG, INFO, WARNING, or ERROR")
    level = getattr(logging, level_name)
    formatter: logging.Formatter = _TextFormatter() if settings.debug else _JsonFormatter()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    standard = logging.StreamHandler(sys.stdout)
    standard.setLevel(level)
    standard.addFilter(_MaximumLevelFilter(logging.INFO))
    standard.addFilter(_ContextFilter())
    standard.setFormatter(formatter)
    failures = logging.StreamHandler(sys.stderr)
    failures.setLevel(max(level, logging.WARNING))
    failures.addFilter(_ContextFilter())
    failures.setFormatter(formatter)
    root.addHandler(standard)
    root.addHandler(failures)
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger(__name__).info(
        "app_logging_configured",
        extra={"event": "app_logging_configured", "debug": settings.debug, "log_level": level_name},
    )


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(level, event, extra={"event": event, **_safe_fields(fields)})


def _relative_filename(filename: str) -> str:
    path = Path(filename)
    parts = path.parts
    if "offerpilot" in parts:
        return "/".join(parts[parts.index("offerpilot"):])
    return path.name


def _sanitized_stack(exc: BaseException) -> list[str]:
    entries: list[str] = []
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        trace = traceback.TracebackException.from_exception(current, capture_locals=False)
        entries.append(current.__class__.__name__)
        if trace.stack:
            entries.extend(
                f"{_relative_filename(frame.filename)}:{frame.lineno} in {frame.name}"
                for frame in trace.stack[-30:]
            )
        current = current.__cause__ or current.__context__
    return entries


def log_exception(logger: logging.Logger, event: str, exc: BaseException, *, include_stack: bool, **fields: Any) -> None:
    payload = {"exception_type": exc.__class__.__name__, **fields}
    if include_stack:
        payload["stack"] = _sanitized_stack(exc)
    log_event(logger, logging.ERROR, event, **payload)
