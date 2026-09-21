"""Idempotent per-Run call ledger and Decimal cost aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from offerpilot.database.connection import get_db
from offerpilot.database.values import dumps, loads, now


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _cost(
    input_tokens: int | None,
    output_tokens: int | None,
    input_price: object,
    output_price: object,
) -> str:
    input_rate = _decimal(input_price)
    output_rate = _decimal(output_price)
    if input_rate is None and output_rate is None:
        return ""
    if (input_rate is not None and input_tokens is None) or (output_rate is not None and output_tokens is None):
        return ""
    amount = (
        Decimal(input_tokens or 0) * (input_rate or Decimal("0"))
        + Decimal(output_tokens or 0) * (output_rate or Decimal("0"))
    ) / Decimal(1_000_000)
    return format(amount, "f")


def _audio_cost(audio_seconds: float | None, audio_price_per_minute: object) -> str:
    rate = _decimal(audio_price_per_minute)
    if audio_seconds is None or rate is None:
        return ""
    return format(Decimal(str(audio_seconds)) * rate / Decimal(60), "f")


def _row(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = loads(row["metadata"], {})
    result["usage_known"] = bool(row["usage_known"])
    result["price_known"] = bool(row["price_known"])
    return result


def begin_call(
    *,
    run_id: str,
    session_id: str,
    profile_id: str,
    logical_call_id: str,
    call_type: str,
    provider: str = "",
    model: str = "",
    operation_name: str = "",
    parent_call_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if call_type not in {"llm", "embedding", "asr", "tool"}:
        raise ValueError("invalid_call_type")
    timestamp = now()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO run_calls(run_id, session_id, profile_id, logical_call_id, parent_call_id, call_type, provider, model, operation_name, started_at, metadata) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, profile_id, logical_call_id) DO NOTHING",
            (run_id, session_id, profile_id, logical_call_id, parent_call_id, call_type, provider, model, operation_name, timestamp, dumps(metadata or {})),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM run_calls WHERE run_id = ? AND profile_id = ? AND logical_call_id = ?",
            (run_id, profile_id, logical_call_id),
        ).fetchone()
        if row is None:
            raise LookupError("run_call_not_found")
        return _row(row)
    finally:
        conn.close()


def record_attempt(run_id: str, profile_id: str, logical_call_id: str, attempt: int, retry_count: int) -> None:
    conn = get_db()
    try:
        conn.execute(
            "UPDATE run_calls SET attempt_count = MAX(attempt_count, ?), retry_count = MAX(retry_count, ?) "
            "WHERE run_id = ? AND profile_id = ? AND logical_call_id = ?",
            (attempt, retry_count, run_id, profile_id, logical_call_id),
        )
        conn.commit()
    finally:
        conn.close()


def finish_call(
    *,
    run_id: str,
    profile_id: str,
    logical_call_id: str,
    status: str,
    started_at: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    first_token_ms: int | None = None,
    audio_seconds: float | None = None,
    error_category: str = "",
    price_version: str = "",
    price_source_url: str = "",
    price_currency: str = "",
    price_effective_at: str = "",
    input_price_per_million: str = "",
    output_price_per_million: str = "",
    audio_price_per_minute: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if status not in {"succeeded", "failed", "cancelled"}:
        raise ValueError("invalid_call_status")
    usage_known = int(any(value is not None for value in (input_tokens, output_tokens, total_tokens, reasoning_tokens)))
    price_known = int(bool(price_currency and (input_price_per_million or output_price_per_million or audio_price_per_minute)))
    try:
        duration_ms = max(0, int(round((datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds() * 1000)))
    except ValueError:
        duration_ms = 0
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT call_type FROM run_calls WHERE run_id = ? AND profile_id = ? AND logical_call_id = ?",
            (run_id, profile_id, logical_call_id),
        ).fetchone()
        billable = existing is not None and existing["call_type"] != "tool"
        estimated_cost = (
            _audio_cost(audio_seconds, audio_price_per_minute)
            if existing is not None and existing["call_type"] == "asr"
            else _cost(input_tokens, output_tokens, input_price_per_million, output_price_per_million)
        )
        if not billable:
            unknown_reason = ""
        elif not price_known:
            unknown_reason = "price_unknown"
        elif not estimated_cost:
            unknown_reason = "audio_duration_unknown" if existing is not None and existing["call_type"] == "asr" else "usage_unknown"
        else:
            unknown_reason = ""
        conn.execute(
            "UPDATE run_calls SET status = ?, ended_at = ?, duration_ms = ?, input_tokens = ?, output_tokens = ?, total_tokens = ?, reasoning_tokens = ?, "
            "first_token_ms = ?, audio_seconds = ?, error_category = ?, price_version = ?, price_source_url = ?, price_currency = ?, price_effective_at = ?, "
            "input_price_per_million = ?, output_price_per_million = ?, audio_price_per_minute = ?, estimated_cost = ?, usage_known = ?, price_known = ?, unknown_reason = ?, metadata = ? "
            "WHERE run_id = ? AND profile_id = ? AND logical_call_id = ?",
            (status, now(), duration_ms, input_tokens, output_tokens, total_tokens, reasoning_tokens, first_token_ms, audio_seconds, error_category, price_version, price_source_url, price_currency, price_effective_at, input_price_per_million, output_price_per_million, audio_price_per_minute, estimated_cost, usage_known, price_known, unknown_reason, dumps(metadata or {}), run_id, profile_id, logical_call_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM run_calls WHERE run_id = ? AND profile_id = ? AND logical_call_id = ?", (run_id, profile_id, logical_call_id)).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def list_calls(run_id: str, profile_id: str, limit: int = 500) -> list[dict[str, Any]] | None:
    conn = get_db()
    try:
        owner = conn.execute("SELECT 1 FROM runs WHERE id = ? AND profile_id = ?", (run_id, profile_id)).fetchone()
        if owner is None:
            return None
        rows = conn.execute("SELECT * FROM run_calls WHERE run_id = ? AND profile_id = ? ORDER BY id ASC LIMIT ?", (run_id, profile_id, max(1, min(limit, 1000)))).fetchall()
        return [_row(row) for row in rows]
    finally:
        conn.close()


def aggregate_calls(run_id: str, profile_id: str) -> dict[str, Any]:
    calls = list_calls(run_id, profile_id) or []
    by_type = {name: {"calls": 0, "succeeded": 0, "failed": 0, "retries": 0} for name in ("llm", "embedding", "asr", "tool")}
    token_total = 0
    unknown_tokens = 0
    audio_seconds = Decimal("0")
    costs: dict[str, Decimal] = {}
    unknown_reasons: set[str] = set()
    for call in calls:
        bucket = by_type[call["call_type"]]
        bucket["calls"] += 1
        if call["status"] in {"succeeded", "failed"}:
            bucket[call["status"]] += 1
        bucket["retries"] += int(call["retry_count"])
        if call["call_type"] in {"llm", "embedding"}:
            if call["total_tokens"] is None:
                unknown_tokens += 1
            else:
                token_total += int(call["total_tokens"])
        if call["audio_seconds"] is not None:
            audio_seconds += Decimal(str(call["audio_seconds"]))
        currency = str(call["price_currency"] or "")
        cost = _decimal(call["estimated_cost"])
        if currency and cost is not None:
            costs[currency] = costs.get(currency, Decimal("0")) + cost
        if call["unknown_reason"]:
            unknown_reasons.add(str(call["unknown_reason"]))
    return {
        "calls_by_type": by_type,
        "call_count": len(calls),
        "success_count": sum(1 for call in calls if call["status"] == "succeeded"),
        "failure_count": sum(1 for call in calls if call["status"] == "failed"),
        "retry_count": sum(int(call["retry_count"]) for call in calls),
        "total_tokens": token_total,
        "unknown_token_calls": unknown_tokens,
        "audio_seconds": format(audio_seconds, "f"),
        "estimated_costs": {currency: format(amount, "f") for currency, amount in costs.items()},
        "cost_unknown_reasons": sorted(unknown_reasons),
    }
