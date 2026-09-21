"""Operational fault-injection contracts for the Run kernel."""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import pytest

from offerpilot.core.config import settings
from offerpilot.core.logging import log_event
from offerpilot.database.connection import get_db, init_db
from offerpilot.runs.events import stream
from offerpilot.runs.service import RunService
from offerpilot.approvals.repository import create_approval
from offerpilot.runs.repository import (
    create_run,
    events_after,
    get_run,
    transition_run,
)
from offerpilot.runs.recovery import expire_approvals, recover_runs
from offerpilot.sessions.repository import create_session


PROFILE_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def initialized_database():
    init_db()


def test_database_lock_maps_to_a_stable_retryable_api_error(client, monkeypatch):
    import offerpilot.runs.api as run_api

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(run_api, "create_run", locked)
    session = client.post("/api/sessions", json={}).json()
    response = client.post(
        f"/api/sessions/{session['id']}/runs",
        headers={"Idempotency-Key": "database-lock"},
        json={"type": "coach", "input": {"message": "开始练习"}},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_busy"


def test_admin_runtime_routes_require_the_admin_key(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_key", "test-admin-key")
    assert client.get("/api/admin/runs").status_code == 403
    assert client.get("/api/admin/runs", headers={"X-OfferPilot-Admin-Key": "wrong"}).status_code == 403
    assert client.get("/api/admin/runs", headers={"X-OfferPilot-Admin-Key": "test-admin-key"}).status_code == 200


@pytest.mark.asyncio
async def test_sse_broker_replays_durable_events_after_disconnect():
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "coach", {"message": "练习"}, "sse-replay")
    transition_run(run["id"], "cancelled", error_code="cancelled", event_type="run_cancelled")
    replayed = [event async for event in stream(run["id"], after=0)]
    assert [event["sequence"] for event in replayed] == [1, 2, 3]
    assert [event["type"] for event in replayed] == ["run_created", "run_complete", "run_cancelled"]
    assert all(event["trace_id"] == run["id"] for event in replayed)
    assert replayed[1]["data"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_race_emits_a_single_terminal_event(monkeypatch):
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "diagnosis", {"question": "Q", "answer": "A"}, "cancel-race")
    service = RunService()

    async def slow_diagnosis(_run, cancel_event):
        await cancel_event.wait()
        raise asyncio.CancelledError()

    monkeypatch.setattr(service, "_diagnosis", slow_diagnosis)
    service.start(run["id"])
    for _ in range(20):
        if get_run(run["id"], PROFILE_ID)["status"] == "running":
            break
        await asyncio.sleep(0.01)
    assert get_run(run["id"], PROFILE_ID)["status"] == "running"

    assert await service.cancel_owned_runs(PROFILE_ID, session_id=session["id"], timeout=1.0)
    assert get_run(run["id"], PROFILE_ID)["status"] == "cancelled"
    terminal_events = [event for event in events_after(run["id"]) if event["type"] == "run_cancelled"]
    assert len(terminal_events) == 1


def test_terminal_event_contains_bounded_queue_approval_active_and_total_timing(monkeypatch):
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "report_export", {"report_id": "report-1"}, "timing-completed")
    approval_id = create_approval(
        run["id"], session["id"], PROFILE_ID, "export_report", "high", {"report_id": "report-1"}, "export"
    )
    conn = get_db()
    try:
        conn.execute(
            "UPDATE runs SET created_at = ?, started_at = ?, status = 'running' WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:02+00:00", run["id"]),
        )
        conn.execute(
            "UPDATE approvals SET created_at = ?, resolved_at = ?, status = 'approved', decision = 'approve' WHERE id = ?",
            ("2026-01-01T00:00:03+00:00", "2026-01-01T00:00:07+00:00", approval_id),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr("offerpilot.runs.repository.now", lambda: "2026-01-01T00:00:10+00:00")
    transition_run(run["id"], "completed", event_type="run_completed", event_data={"success": True})
    timing = events_after(run["id"])[-1]["data"]["timing"]
    assert timing == {
        "queue_duration_ms": 2000,
        "approval_wait_ms": 4000,
        "active_duration_ms": 4000,
        "total_duration_ms": 10000,
    }


def test_pending_cancellation_counts_all_elapsed_time_as_queue(monkeypatch):
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "coach", {"message": "练习"}, "timing-pending-cancel")
    conn = get_db()
    try:
        conn.execute("UPDATE runs SET created_at = ? WHERE id = ?", ("2026-01-01T00:00:00+00:00", run["id"]))
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr("offerpilot.runs.repository.now", lambda: "2026-01-01T00:00:05+00:00")
    transition_run(run["id"], "cancelled", error_code="cancelled", event_type="run_cancelled")
    timing = events_after(run["id"])[-1]["data"]["timing"]
    assert timing == {
        "queue_duration_ms": 5000,
        "approval_wait_ms": 0,
        "active_duration_ms": 0,
        "total_duration_ms": 5000,
    }


@pytest.mark.parametrize("status,event_type", [("completed", "run_completed"), ("failed", "run_failed")])
def test_terminal_timing_without_approval_assigns_elapsed_work_to_active(monkeypatch, status, event_type):
    session = create_session(PROFILE_ID)
    run, _ = create_run(
        PROFILE_ID,
        session["id"],
        "coach",
        {"message": "练习"},
        f"timing-no-approval-{status}",
    )
    conn = get_db()
    try:
        conn.execute(
            "UPDATE runs SET status = 'running', created_at = ?, started_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00", run["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr("offerpilot.runs.repository.now", lambda: "2026-01-01T00:00:06+00:00")
    transition_run(run["id"], status, error_code="test_failure" if status == "failed" else "", event_type=event_type)
    timing = events_after(run["id"])[-1]["data"]["timing"]
    assert timing == {
        "queue_duration_ms": 1000,
        "approval_wait_ms": 0,
        "active_duration_ms": 5000,
        "total_duration_ms": 6000,
    }


def test_denied_approval_timing_stops_wait_at_decision(monkeypatch):
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "report_export", {"report_id": "report-1"}, "timing-denied")
    approval_id = create_approval(
        run["id"], session["id"], PROFILE_ID, "export_report", "high", {"report_id": "report-1"}, "export"
    )
    conn = get_db()
    try:
        conn.execute(
            "UPDATE runs SET status = 'waiting_approval', created_at = ?, started_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00", run["id"]),
        )
        conn.execute(
            "UPDATE approvals SET status = 'denied', decision = 'deny', created_at = ?, resolved_at = ? WHERE id = ?",
            ("2026-01-01T00:00:02+00:00", "2026-01-01T00:00:05+00:00", approval_id),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr("offerpilot.runs.repository.now", lambda: "2026-01-01T00:00:07+00:00")
    transition_run(run["id"], "cancelled", error_code="permission_denied", event_type="run_cancelled")
    timing = events_after(run["id"])[-1]["data"]["timing"]
    assert timing == {
        "queue_duration_ms": 1000,
        "approval_wait_ms": 3000,
        "active_duration_ms": 3000,
        "total_duration_ms": 7000,
    }


def test_expired_approval_cancels_run_with_complete_timing(monkeypatch):
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "report_export", {"report_id": "report-1"}, "timing-expired")
    approval_id = create_approval(
        run["id"], session["id"], PROFILE_ID, "export_report", "high", {"report_id": "report-1"}, "export"
    )
    conn = get_db()
    try:
        conn.execute(
            "UPDATE runs SET status = 'waiting_approval', created_at = ?, started_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00", run["id"]),
        )
        conn.execute(
            "UPDATE approvals SET created_at = ?, expires_at = ? WHERE id = ?",
            ("2026-01-01T00:00:02+00:00", "2026-01-01T00:00:04+00:00", approval_id),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr("offerpilot.runs.repository.now", lambda: "2026-01-01T00:00:07+00:00")
    assert expire_approvals()[0]["run_id"] == run["id"]
    terminal = events_after(run["id"])[-1]
    assert terminal["type"] == "run_cancelled"
    assert terminal["data"]["reason"] == "approval_expired"
    timing = terminal["data"]["timing"]
    assert set(timing) == {"queue_duration_ms", "approval_wait_ms", "active_duration_ms", "total_duration_ms"}
    assert all(isinstance(value, int) and value >= 0 for value in timing.values())


def test_restart_interruption_persists_timing_and_minimal_operation_logs(monkeypatch):
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "diagnosis", {"question": "Q", "answer": "A"}, "timing-restart")
    conn = get_db()
    try:
        conn.execute(
            "UPDATE runs SET status = 'running', created_at = ?, started_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00", run["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr("offerpilot.runs.repository.now", lambda: "2026-01-01T00:00:04+00:00")
    assert recover_runs() == [run["id"]]
    terminal = events_after(run["id"])[-1]
    assert terminal["type"] == "run_interrupted"
    assert terminal["data"]["timing"] == {
        "queue_duration_ms": 1000,
        "approval_wait_ms": 0,
        "active_duration_ms": 3000,
        "total_duration_ms": 4000,
    }
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT event_type, run_id, metadata FROM operation_logs WHERE event_type IN ('run_interrupted', 'runtime_recovered') ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert [(row["event_type"], row["run_id"]) for row in rows] == [
        ("run_interrupted", run["id"]),
        ("runtime_recovered", None),
    ]
    assert "runtime_interrupted" in rows[0]["metadata"]


@pytest.mark.asyncio
async def test_worker_logs_bind_run_context_and_failed_audit_is_redacted(monkeypatch, caplog):
    session = create_session(PROFILE_ID)
    run, _ = create_run(PROFILE_ID, session["id"], "diagnosis", {"question": "Q", "answer": "A"}, "worker-context")
    service = RunService()
    canary = "secret-answer-C:\\private\\provider-response"
    provider_logger = logging.getLogger("offerpilot.test.provider")

    async def failing_diagnosis(_run, _cancel_event):
        log_event(provider_logger, logging.INFO, "provider_request_completed", provider="test", duration_ms=12)
        raise RuntimeError(canary)

    monkeypatch.setattr(service, "_diagnosis", failing_diagnosis)
    caplog.set_level(logging.INFO)
    await service._execute(run["id"], asyncio.Event())

    provider_record = next(record for record in caplog.records if getattr(record, "event", "") == "provider_request_completed")
    finalized_record = next(record for record in caplog.records if getattr(record, "event", "") == "run_finalized")
    assert provider_record.run_id == run["id"]
    assert provider_record.session_id == session["id"]
    assert finalized_record.run_type == "diagnosis"
    assert finalized_record.total_duration_ms >= 0
    assert get_run(run["id"], PROFILE_ID)["status"] == "failed"

    conn = get_db()
    try:
        audit = conn.execute(
            "SELECT summary, metadata FROM operation_logs WHERE event_type = 'run_failed' AND run_id = ?",
            (run["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert audit is not None
    assert canary not in audit["summary"]
    assert canary not in audit["metadata"]
