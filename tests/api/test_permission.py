"""Regression tests for the durable approval and grant contract."""

import pytest

from offerpilot.coaching.state import claim_approved_approval, create_approval, get_approval, resolve_approval
from offerpilot.core.database import init_db
from offerpilot.permission.permission import RiskLevel, get_tool_risk, permission_gate
from offerpilot.session.session import create_session

PROFILE_ID = "00000000-0000-4000-8000-000000000001"


def test_risk_policy_has_no_formal_diagnosis_tool():
    assert get_tool_risk("search_knowledge") == RiskLevel.LOW
    assert get_tool_risk("transcribe_audio") == RiskLevel.MEDIUM
    assert get_tool_risk("save_memory") == RiskLevel.HIGH
    assert get_tool_risk("run_diagnosis") == RiskLevel.HIGH


def test_policy_only_auto_allows_low_risk():
    assert permission_gate.check("session", "search_knowledge")["allowed"] is True
    assert permission_gate.check("session", "save_memory")["allowed"] is False


def test_persisted_approval_claim_is_idempotent():
    init_db()
    session = create_session(PROFILE_ID)
    request_id = create_approval(
        session["id"],
        PROFILE_ID,
        "save_memory",
        "high",
        {"key": "weakness", "value": "x"},
        flow_kind="coach",
        trace_id="trace-1",
        public_params={"key": "weakness"},
    )
    assert resolve_approval(request_id, session["id"], PROFILE_ID, "approved") is not None
    first = claim_approved_approval(request_id, session["id"], PROFILE_ID)
    assert first is not None and first["status"] == "executing"
    assert claim_approved_approval(request_id, session["id"], PROFILE_ID) is None


def test_unsafe_approval_creation_and_generic_execution_routes_are_removed(client):
    session_id = create_session(PROFILE_ID)["id"]
    assert client.post("/api/permission/check", json={"session_id": session_id, "tool_name": "transcribe_audio"}).status_code == 404
    assert client.post("/api/tools/save-memory", json={"session_id": session_id, "key": "weakness", "value": "x"}).status_code == 404
    assert client.post("/api/permission/resume", json={"session_id": session_id, "request_id": "x"}).status_code == 404


def test_audio_approval_rejects_an_outside_path_without_deleting_it(client):
    from offerpilot.core.config import settings

    session = create_session(PROFILE_ID)
    victim = settings.db_path.parent / "outside-do-not-delete.wav"
    victim.write_bytes(b"sensitive")
    try:
        with pytest.raises(ValueError, match="upload_id"):
            create_approval(
                session["id"],
                PROFILE_ID,
                "transcribe_audio",
                "medium",
                {"filepath": str(victim)},
                flow_kind="audio",
                public_params={"filename": "do-not-delete.wav", "size": len(victim.read_bytes())},
            )
        assert victim.exists()
    finally:
        victim.unlink(missing_ok=True)
