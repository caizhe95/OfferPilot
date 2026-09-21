"""Run API and schema regression tests."""

from __future__ import annotations


def _new_session(client) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 200
    return response.json()["id"]


def test_schema_has_run_tables():
    from offerpilot.database.connection import get_db, init_db

    init_db()
    conn = get_db()
    try:
        table_names = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        }
    finally:
        conn.close()
    expected = {
        "profiles", "sessions", "runs", "run_events", "approvals", "profile_memories",
        "session_summaries", "session_followups", "profile_growth_summaries",
        "diagnosis_reports", "diagnosis_point_results", "knowledge_exam_points", "operation_logs",
    }
    assert expected.issubset(table_names)
    assert not {
        "coach_runs", "traces", "trace_events", "checkpoints", "progress_events", "approval_requests",
    }.intersection(table_names)


def test_old_runtime_routes_are_404(client):
    assert client.post("/api/coach", json={}).status_code == 404
    assert client.get("/api/traces/not-a-run").status_code == 404
    assert client.get("/api/sessions/x/progress").status_code == 404


def test_removed_legacy_endpoints_are_not_registered(client):
    assert client.get("/api/profile").status_code == 404
    assert client.get("/api/capabilities/audio").status_code == 404
    assert client.get("/api/admin/evals/cases").status_code == 404
    assert client.post("/api/admin/evals/run/diagnosis_structure").status_code == 404


def test_admin_eval_run_all_returns_only_immediate_results(client, monkeypatch):
    from offerpilot.core.config import settings

    monkeypatch.setattr(settings, "admin_key", "test-admin-key")
    response = client.post("/api/admin/evals/run-all", headers={"X-OfferPilot-Admin-Key": "test-admin-key"})
    assert response.status_code == 200
    payload = response.json()
    assert {"summary", "total", "passed", "failed", "pass_rate", "results"}.issubset(payload)
    assert "run_id" not in payload


def test_run_snapshot_exposes_public_timing_approval_and_sequence(client):
    from offerpilot.approvals.repository import create_approval
    from offerpilot.runs.repository import claim_pending_run, create_run, transition_run
    from offerpilot.sessions.repository import create_session

    profile_id = "00000000-0000-4000-8000-000000000001"
    session = create_session(profile_id)
    active, _ = create_run(profile_id, session["id"], "coach", {"message": "active"}, "public-active")
    active_snapshot = client.get(f"/api/runs/{active['id']}").json()
    assert active_snapshot["trace_id"] == active["id"]
    assert active_snapshot["timing"] is None
    assert active_snapshot["pending_approval"] is None
    assert active_snapshot["last_event_sequence"] >= 1

    approval_session = create_session(profile_id)
    approval_run, _ = create_run(profile_id, approval_session["id"], "coach", {"message": "approval"}, "public-approval")
    approval_id = create_approval(
        approval_run["id"], approval_run["session_id"], profile_id, "search_knowledge", "medium",
        {"question": "private"}, "coach", {"question": "safe"},
    )
    claim_pending_run(approval_run["id"])
    transition_run(approval_run["id"], "waiting_approval", state={"approval_id": approval_id})
    approval_snapshot = client.get(f"/api/runs/{approval_run['id']}").json()
    assert approval_snapshot["pending_approval"]["id"] == approval_id
    assert approval_snapshot["pending_approval"]["public_params"] == {"question": "safe"}
    assert "params" not in approval_snapshot["pending_approval"]
    assert approval_snapshot["pending_approval"]["trace_id"] == approval_run["id"]

    terminal_session = create_session(profile_id)
    terminal, _ = create_run(profile_id, terminal_session["id"], "report_export", {"report_id": "report"}, "public-terminal")
    claim_pending_run(terminal["id"])
    transition_run(terminal["id"], "completed", event_type="run_completed")
    terminal_snapshot = client.get(f"/api/runs/{terminal['id']}").json()
    assert terminal_snapshot["timing"]["total_duration_ms"] >= 0
    assert terminal_snapshot["last_event_sequence"] >= 2
    assert "params" not in (terminal_snapshot.get("pending_approval") or {})
    listed = client.get(f"/api/sessions/{terminal_session['id']}/runs").json()["runs"]
    assert listed[0]["id"] == terminal["id"]
    assert listed[0]["timing"]["total_duration_ms"] >= 0


def test_coach_state_replays_owned_trace_without_private_approval_params(client):
    from offerpilot.runs.repository import create_run
    from offerpilot.sessions.repository import create_session

    profile_id = "00000000-0000-4000-8000-000000000001"
    session = create_session(profile_id)
    assert client.get(f"/api/coach/state?session_id={session['id']}").json()["run"] is None
    run, _ = create_run(profile_id, session["id"], "coach", {"message": "recover"}, "coach-state")
    response = client.get(f"/api/coach/state?session_id={session['id']}")
    assert response.status_code == 200
    state = response.json()
    assert state["run"]["id"] == run["id"]
    assert state["run"]["trace_id"] == run["id"]
    assert state["trace"][0]["trace_id"] == run["id"]
    assert state["approval"] is None


def test_report_export_endpoint_validates_ownership_before_creating_run(client):
    import uuid

    from offerpilot.database.connection import get_db
    from offerpilot.runs.repository import begin_run, create_run, transition_run
    from offerpilot.sessions.repository import create_session

    profile_id = "00000000-0000-4000-8000-000000000001"
    session = create_session(profile_id)
    run, _ = create_run(
        profile_id,
        session["id"],
        "diagnosis",
        {"question": "Q", "answer": "A"},
        "export-source",
    )
    assert begin_run(run["id"])["status"] == "running"
    transition_run(run["id"], "completed")
    report_id = str(uuid.uuid4())
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO diagnosis_reports(id, run_id, session_id, profile_id, question, answer, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (report_id, run["id"], session["id"], profile_id, "Q", "A", "now"),
        )
        conn.commit()
    finally:
        conn.close()
    response = client.post(
        "/api/coach/reports/export",
        json={"session_id": session["id"], "report_id": report_id},
    )
    assert response.status_code == 202
    assert response.json()["run"]["type"] == "report_export"
    assert client.post(
        "/api/coach/reports/export",
        json={"session_id": session["id"], "report_id": str(uuid.uuid4())},
    ).status_code == 404


def test_idempotency_and_single_active_run(client, monkeypatch):
    from offerpilot.runs.service import run_service

    monkeypatch.setattr(run_service, "start", lambda run_id: None)
    session_id = _new_session(client)
    body = {"type": "coach", "input": {"message": "开始练习"}}
    first = client.post(f"/api/sessions/{session_id}/runs", headers={"Idempotency-Key": "coach-1"}, json=body)
    assert first.status_code == 202
    run_id = first.json()["run"]["id"]
    again = client.post(f"/api/sessions/{session_id}/runs", headers={"Idempotency-Key": "coach-1"}, json=body)
    assert again.status_code == 202
    assert again.json()["reused"] is True
    assert again.json()["run"]["id"] == run_id
    conflict = client.post(
        f"/api/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "coach-2"},
        json={"type": "diagnosis", "input": {"question": "Q", "answer": "A"}},
    )
    assert conflict.status_code == 409


def test_run_input_limits_return_stable_errors(client):
    session_id = _new_session(client)
    response = client.post(
        f"/api/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "coach-too-long"},
        json={"type": "coach", "input": {"message": "x" * 12001}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "coach_message_too_long"

    response = client.post(
        f"/api/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "diagnosis-too-long"},
        json={"type": "diagnosis", "input": {"question": "Q" * 4001, "answer": "A"}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "diagnosis_question_too_long"
