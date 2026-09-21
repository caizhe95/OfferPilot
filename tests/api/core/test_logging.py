"""Privacy-safe application logging contracts."""

from __future__ import annotations

import io
import json
import logging

from offerpilot.core.logging import _JsonFormatter, _TextFormatter, log_exception, request_id_from_header


def test_request_id_is_validated_and_returned(client):
    response = client.get("/health", headers={"X-Request-ID": "edge-request_42"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "edge-request_42"
    assert request_id_from_header("not valid space") != "not valid space"


def test_logging_format_and_sanitized_stack_never_emit_exception_content():
    record = logging.LogRecord("offerpilot.test", logging.INFO, __file__, 1, "ignored", (), None)
    record.event = "run_finalized"
    record.run_id = "run-42"
    record.error_code = "provider_server"
    record.queue_duration_ms = 12
    record.approval_wait_ms = 34
    record.active_duration_ms = 56
    record.total_duration_ms = 102
    record.stream_mode = "stream"
    record.first_token_ms = 7
    record.output_tokens = 55
    record.generated_chars = 700
    payload = json.loads(_JsonFormatter().format(record))
    assert payload["event"] == "run_finalized"
    assert payload["total_duration_ms"] == 102
    assert payload["stream_mode"] == "stream"
    assert payload["output_tokens"] == 55
    assert "event=run_finalized" in _TextFormatter().format(record)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger("offerpilot.test.sanitized")
    previous_handlers = logger.handlers[:]
    previous_propagate = logger.propagate
    previous_level = logger.level
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.ERROR)
    canary = "secret-answer-and-C:\\private\\provider-response"
    try:
        try:
            raise RuntimeError(canary)
        except RuntimeError as exc:
            log_exception(logger, "run_worker_failed", exc, include_stack=True, error_code="run_failed")
    finally:
        logger.handlers = previous_handlers
        logger.propagate = previous_propagate
        logger.setLevel(previous_level)

    output = stream.getvalue()
    assert canary not in output
    assert "C:\\private" not in output
    assert "RuntimeError" in output
    assert "test_logging.py" in output
