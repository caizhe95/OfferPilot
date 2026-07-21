"""Tests for permission gate, approval flow, and audit logging."""

import pytest
from app.core.database import init_db
from app.permission.permission import (
    permission_gate,
    write_audit_log,
    get_audit_logs,
    get_tool_risk,
    RiskLevel,
    TOOL_RISK_MAP,
)


class TestRiskClassification:
    """Tests for tool risk level classification."""

    def test_low_risk_tools(self):
        assert get_tool_risk("search_knowledge") == RiskLevel.LOW
        assert get_tool_risk("diagnose_interview") == RiskLevel.LOW

    def test_medium_risk_tools(self):
        assert get_tool_risk("transcribe_audio") == RiskLevel.MEDIUM

    def test_high_risk_tools(self):
        assert get_tool_risk("save_memory") == RiskLevel.HIGH
        assert get_tool_risk("export_report") == RiskLevel.HIGH

    def test_unknown_tool_defaults_to_high(self):
        assert get_tool_risk("unknown_tool") == RiskLevel.HIGH


class TestPermissionGate:
    """Tests for the permission gate."""

    def setup_method(self):
        permission_gate.clear_session("test-session")
        permission_gate.clear_session("test-session-2")

    def test_low_risk_auto_allowed(self):
        result = permission_gate.check("test-session", "search_knowledge")
        assert result["allowed"] is True
        assert result["risk_level"] == "low"

    def test_medium_risk_first_time_needs_approval(self):
        result = permission_gate.check("test-session", "transcribe_audio")
        assert result["allowed"] is False
        assert result["risk_level"] == "medium"
        assert "request_id" in result

    def test_medium_risk_remembered_after_approve(self):
        result = permission_gate.check("test-session", "transcribe_audio")
        request_id = result["request_id"]

        # Approve
        pending = permission_gate.approve(request_id)
        assert pending is not None
        assert pending["tool_name"] == "transcribe_audio"

        # Second call should be auto-allowed
        result2 = permission_gate.check("test-session", "transcribe_audio")
        assert result2["allowed"] is True

    def test_high_risk_always_needs_approval(self):
        # First call
        result1 = permission_gate.check("test-session", "save_memory")
        assert result1["allowed"] is False

        # Approve
        permission_gate.approve(result1["request_id"])

        # Second call still needs approval (high risk)
        result2 = permission_gate.check("test-session", "save_memory")
        assert result2["allowed"] is False

    def test_critical_risk_denied(self):
        # Register a critical tool
        from app.permission.permission import TOOL_RISK_MAP as risk_map
        risk_map["dangerous_tool"] = RiskLevel.CRITICAL

        result = permission_gate.check("test-session", "dangerous_tool")
        assert result["allowed"] is False
        assert result["risk_level"] == "critical"
        assert "denied" in result.get("reason", "").lower()

        # Cleanup
        risk_map.pop("dangerous_tool", None)

    def test_deny_removes_pending(self):
        result = permission_gate.check("test-session", "transcribe_audio")
        request_id = result["request_id"]

        pending = permission_gate.deny(request_id)
        assert pending is not None

        # Denying again should return None
        assert permission_gate.deny(request_id) is None
        assert permission_gate.approve(request_id) is None

    def test_approve_nonexistent_returns_none(self):
        assert permission_gate.approve("nonexistent") is None

    def test_deny_nonexistent_returns_none(self):
        assert permission_gate.deny("nonexistent") is None

    def test_clear_session_removes_approvals(self):
        # Approve medium tool
        result = permission_gate.check("test-session", "transcribe_audio")
        permission_gate.approve(result["request_id"])

        # Clear session
        permission_gate.clear_session("test-session")

        # Should need approval again
        result2 = permission_gate.check("test-session", "transcribe_audio")
        assert result2["allowed"] is False

    def test_different_sessions_independent(self):
        # Approve in session 1
        result = permission_gate.check("test-session", "transcribe_audio")
        permission_gate.approve(result["request_id"])

        # Session 2 still needs approval
        result2 = permission_gate.check("test-session-2", "transcribe_audio")
        assert result2["allowed"] is False


class TestAuditLog:
    """Tests for audit logging."""

    def setup_method(self):
        from app.session.session import create_session
        init_db()
        self.session = create_session()
        self.session_id = self.session["id"]

    def test_write_audit_log(self):
        entry = write_audit_log(
            session_id=self.session_id,
            tool_name="save_memory",
            risk_level="high",
            action="request",
            params={"key": "weakness", "value": "poor structure"},
        )
        assert entry["tool_name"] == "save_memory"
        assert entry["action"] == "request"
        assert "id" in entry

    def test_get_audit_logs(self):
        write_audit_log(self.session_id, "save_memory", "high", "request")
        write_audit_log(self.session_id, "save_memory", "high", "approve")

        logs = get_audit_logs(self.session_id)
        assert len(logs) >= 2
        # Most recent first
        assert logs[0]["action"] == "approve"

    def test_audit_log_approve_deny_cycle(self):
        # Request
        write_audit_log(self.session_id, "transcribe_audio", "medium", "request")
        # Deny
        write_audit_log(self.session_id, "transcribe_audio", "medium", "deny")
        # Another request
        write_audit_log(self.session_id, "transcribe_audio", "medium", "request")
        # Approve
        write_audit_log(self.session_id, "transcribe_audio", "medium", "approve")

        logs = get_audit_logs(self.session_id)
        actions = [l["action"] for l in logs]
        assert "request" in actions
        assert "approve" in actions
        assert "deny" in actions


# ---------------------------------------------------------------------------
# Stage 4: Resume endpoint tests (using FastAPI TestClient)
# ---------------------------------------------------------------------------

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import init_db
from app.permission.permission import permission_gate
from app.session.session import create_session


@pytest.fixture
def perm_client():
    """Test client with clean DB."""
    init_db()
    client = TestClient(app)
    client.headers.update({"X-OfferPilot-Profile-Id": "00000000-0000-4000-8000-000000000001"})
    return client


class TestResumeFlow:
    """Tests for approve → resume → execute flow."""

    def test_save_memory_returns_permission_required(self, perm_client):
        """save-memory tool API should return permission_required for high risk."""
        session_id = create_session()["id"]
        resp = perm_client.post("/api/tools/save-memory", json={
            "session_id": session_id,
            "key": "weakness",
            "value": "test value",
            "category": "diagnosis",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("permission_required") is True
        assert data.get("type") == "permission_required"
        assert data.get("tool_name") == "save_memory"
        assert data.get("session_id") == session_id
        assert "request_id" in data
        assert data.get("risk_level") == "high"

        session_resp = perm_client.get(f"/api/sessions/{session_id}")
        assert session_resp.status_code == 200
        assert session_resp.json()["status"] == "waiting_approval"

    def test_approve_resume_execute_save_memory(self, perm_client):
        """Approve then resume should execute save_memory and write audit."""
        session_id = create_session()["id"]

        # 1. Request save-memory (should trigger permission_required)
        resp = perm_client.post("/api/tools/save-memory", json={
            "session_id": session_id,
            "key": "weakness",
            "value": "test weakness value",
            "category": "diagnosis",
        })
        data = resp.json()
        request_id = data["request_id"]

        # 2. Approve
        approve_resp = perm_client.post("/api/permission/approve", json={
            "request_id": request_id,
            "session_id": session_id,
        })
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "approved"

        # 3. Resume (execute)
        resume_resp = perm_client.post("/api/permission/resume", json={
            "request_id": request_id,
            "session_id": session_id,
        })
        assert resume_resp.status_code == 200
        resume_data = resume_resp.json()
        assert resume_data["status"] == "executed"

        # 4. Check audit log (approve + execute tracked)
        audit_resp = perm_client.get(f"/api/permission/audit/{session_id}")
        assert audit_resp.status_code == 200
        logs = audit_resp.json()["audit_logs"]
        actions = [l["action"] for l in logs]
        assert "request" in actions
        assert "approve" in actions
        assert "execute" in actions

        session_resp = perm_client.get(f"/api/sessions/{session_id}")
        assert session_resp.status_code == 200
        assert session_resp.json()["status"] == "ready"

    def test_resume_without_approve_fails(self, perm_client):
        """Resume without prior approve should fail."""
        session_id = create_session()["id"]

        # Request save-memory
        resp = perm_client.post("/api/tools/save-memory", json={
            "session_id": session_id,
            "key": "weakness",
            "value": "test",
        })
        request_id = resp.json()["request_id"]

        # Try resume without approve
        resume_resp = perm_client.post("/api/permission/resume", json={
            "request_id": request_id,
            "session_id": session_id,
        })
        assert resume_resp.status_code in (400, 404)  # Not approved

    def test_deny_marks_session_failed_and_audits(self, perm_client):
        """Denying a pending high-risk tool should fail session and not execute."""
        session_id = create_session()["id"]
        resp = perm_client.post("/api/tools/save-memory", json={
            "session_id": session_id,
            "key": "weakness",
            "value": "denied value",
        })
        request_id = resp.json()["request_id"]

        deny_resp = perm_client.post("/api/permission/deny", json={
            "request_id": request_id,
            "session_id": session_id,
        })
        assert deny_resp.status_code == 200
        assert deny_resp.json()["status"] == "denied"

        session_resp = perm_client.get(f"/api/sessions/{session_id}")
        assert session_resp.json()["status"] == "failed"

        audit_resp = perm_client.get(f"/api/permission/audit/{session_id}")
        actions = [l["action"] for l in audit_resp.json()["audit_logs"]]
        assert "request" in actions
        assert "deny" in actions
