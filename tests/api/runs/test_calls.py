"""Run call ledger, aggregation, pricing, and ownership contracts."""

from __future__ import annotations

from offerpilot.profiles.cookies import PROFILE_COOKIE, make_profile_cookie
from offerpilot.runs.calls import aggregate_calls, begin_call, finish_call, list_calls, record_attempt
from offerpilot.runs.repository import create_run
from offerpilot.sessions.repository import create_session

PROFILE_ID = "00000000-0000-4000-8000-000000000001"
OTHER_PROFILE_ID = "00000000-0000-4000-8000-000000000002"


def _run() -> tuple[dict, dict]:
    from offerpilot.database.connection import init_db

    init_db()
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "coach", {"message": "metrics"}, "metrics")
    return session, run


def _begin(run: dict, logical_id: str, call_type: str = "llm") -> dict:
    return begin_call(
        run_id=run["id"], session_id=run["session_id"], profile_id=PROFILE_ID,
        logical_call_id=logical_id, call_type=call_type, provider="test", model="test-model",
        operation_name="test_call",
    )


def _finish(run: dict, call: dict, **kwargs) -> dict:
    result = finish_call(
        run_id=run["id"], profile_id=PROFILE_ID, logical_call_id=call["logical_call_id"],
        status="succeeded", started_at=call["started_at"], **kwargs,
    )
    assert result is not None
    return result


def test_usage_complete_partial_and_missing_remain_explicit():
    _session, run = _run()
    complete = _finish(run, _begin(run, "usage:complete"), input_tokens=10, output_tokens=5, total_tokens=15)
    partial = _finish(run, _begin(run, "usage:partial"), input_tokens=7)
    missing = _finish(run, _begin(run, "usage:missing"))

    assert complete["usage_known"] is True
    assert partial["usage_known"] is True and partial["output_tokens"] is None and partial["total_tokens"] is None
    assert missing["usage_known"] is False
    metrics = aggregate_calls(run["id"], PROFILE_ID)
    assert metrics["total_tokens"] == 15
    assert metrics["unknown_token_calls"] == 2


def test_decimal_costs_are_exact_and_kept_separate_by_currency():
    _session, run = _run()
    usd = _finish(
        run, _begin(run, "price:usd"), input_tokens=1, output_tokens=1, total_tokens=2,
        price_version="test-v1", price_source_url="https://example.test/usd", price_currency="USD",
        price_effective_at="2026-09-21", input_price_per_million="0.1", output_price_per_million="0.2",
    )
    cny = _finish(
        run, _begin(run, "price:cny"), input_tokens=3, output_tokens=2, total_tokens=5,
        price_version="test-v1", price_source_url="https://example.test/cny", price_currency="CNY",
        price_effective_at="2026-09-21", input_price_per_million="0.333333", output_price_per_million="0.666667",
    )
    unknown = _finish(run, _begin(run, "price:unknown"), input_tokens=2, output_tokens=2, total_tokens=4)

    assert usd["estimated_cost"] == "0.0000003"
    assert cny["estimated_cost"] == "0.000002333333"
    assert unknown["estimated_cost"] == "" and unknown["unknown_reason"] == "price_unknown"
    assert aggregate_calls(run["id"], PROFILE_ID)["estimated_costs"] == {
        "USD": "0.0000003", "CNY": "0.000002333333",
    }


def test_known_price_with_partial_usage_has_unknown_cost():
    _session, run = _run()
    call = _finish(
        run, _begin(run, "price:partial-usage"), input_tokens=9,
        price_version="test-v1", price_source_url="https://example.test", price_currency="USD",
        price_effective_at="2026-09-21", input_price_per_million="1", output_price_per_million="2",
    )
    assert call["usage_known"] is True
    assert call["estimated_cost"] == ""
    assert call["unknown_reason"] == "usage_unknown"


def test_asr_price_uses_decimal_audio_minutes():
    _session, run = _run()
    call = _finish(
        run, _begin(run, "asr:priced", "asr"), audio_seconds=90,
        price_version="test-v1", price_source_url="https://example.test/asr", price_currency="CNY",
        price_effective_at="2026-09-21", audio_price_per_minute="0.12",
    )
    assert call["estimated_cost"] == "0.18"
    assert call["unknown_reason"] == ""
    assert aggregate_calls(run["id"], PROFILE_ID)["estimated_costs"] == {"CNY": "0.18"}


def test_logical_call_is_idempotent_and_attempts_update_one_row():
    _session, run = _run()
    first = _begin(run, "retry:same")
    second = _begin(run, "retry:same")
    assert first["id"] == second["id"]
    record_attempt(run["id"], PROFILE_ID, "retry:same", 1, 0)
    record_attempt(run["id"], PROFILE_ID, "retry:same", 3, 2)
    _finish(run, first, input_tokens=10, output_tokens=5, total_tokens=15)
    _finish(run, second, input_tokens=10, output_tokens=5, total_tokens=15)

    calls = list_calls(run["id"], PROFILE_ID)
    assert calls is not None and len(calls) == 1
    assert calls[0]["attempt_count"] == 3 and calls[0]["retry_count"] == 2
    assert aggregate_calls(run["id"], PROFILE_ID)["total_tokens"] == 15


def test_tool_does_not_duplicate_provider_cost_or_unknown_usage():
    _session, run = _run()
    tool = _finish(run, _begin(run, "tool:search", "tool"))
    _finish(
        run, _begin(run, "embedding:search", "embedding"), input_tokens=100, output_tokens=0, total_tokens=100,
        price_version="test-v1", price_source_url="https://example.test", price_currency="USD",
        price_effective_at="2026-09-21", input_price_per_million="1", output_price_per_million="0",
    )
    metrics = aggregate_calls(run["id"], PROFILE_ID)
    assert tool["unknown_reason"] == ""
    assert metrics["unknown_token_calls"] == 0
    assert metrics["estimated_costs"] == {"USD": "0.0001"}


def test_calls_api_is_profile_scoped_and_run_snapshot_has_metrics(client):
    _session, run = _run()
    _finish(run, _begin(run, "api:call"), input_tokens=4, output_tokens=6, total_tokens=10)

    response = client.get(f"/api/runs/{run['id']}/calls")
    assert response.status_code == 200
    assert response.json()["calls"][0]["logical_call_id"] == "api:call"
    snapshot = client.get(f"/api/runs/{run['id']}").json()
    assert snapshot["metrics"]["call_count"] == 1
    assert snapshot["metrics"]["total_tokens"] == 10

    client.cookies.set(PROFILE_COOKIE, make_profile_cookie(OTHER_PROFILE_ID))
    assert client.get(f"/api/runs/{run['id']}/calls").status_code == 404
