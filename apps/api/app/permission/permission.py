"""Permission gate and audit system.

Implements tool risk classification, permission checks, approval flow,
and audit logging.
"""

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from app.core.database import get_db


class RiskLevel(str, Enum):
    LOW = "low"           # Auto-allow
    MEDIUM = "medium"     # First-time confirm, can remember
    HIGH = "high"         # Every-time confirm or user explicitly enables
    CRITICAL = "critical" # Default deny


# Tool registry with risk levels
TOOL_RISK_MAP: dict[str, RiskLevel] = {
    "search_knowledge": RiskLevel.LOW,
    "get_practice_profile": RiskLevel.LOW,
    "list_recent_reports": RiskLevel.LOW,
    "recommend_next_question": RiskLevel.LOW,
    "run_diagnosis": RiskLevel.LOW,
    "diagnose_interview": RiskLevel.LOW,
    "transcribe_audio": RiskLevel.MEDIUM,
    "save_memory": RiskLevel.HIGH,
    "export_report": RiskLevel.HIGH,
}


def get_tool_risk(tool_name: str) -> RiskLevel:
    """Get risk level for a tool. Defaults to HIGH for unknown tools."""
    return TOOL_RISK_MAP.get(tool_name, RiskLevel.HIGH)


def build_permission_required_event(
    session_id: str,
    tool_name: str,
    permission_result: dict,
    params: dict | None = None,
    message: str | None = None,
) -> dict:
    """Build the canonical permission_required event/response."""
    return {
        "type": "permission_required",
        "permission_required": True,
        "session_id": session_id,
        "request_id": permission_result.get("request_id", ""),
        "tool_name": tool_name,
        "risk_level": permission_result.get("risk_level", get_tool_risk(tool_name).value),
        "params": params or {},
        "message": message or "Tool call requires user approval",
    }


class PermissionGate:
    """Gate that checks whether a tool call needs approval.

    In auto mode (planned for later), low-risk tools pass automatically.
    Medium/high tools require approval, creating a pending request.
    """

    def __init__(self):
        self._approved_tools: dict[str, set[str]] = {}  # session_id -> {tool_name}
        self._pending_requests: dict[str, dict] = {}     # request_id -> pending info
        self._approved_params: dict[str, dict] = {}      # request_id -> approved params for resume

    def check(self, session_id: str, tool_name: str, params: dict | None = None) -> dict:
        """Check if a tool call is allowed.

        Returns:
            dict with:
                allowed: bool
                risk_level: str
                request_id: str (if approval needed)
                reason: str (if denied)
        """
        risk = get_tool_risk(tool_name)

        if risk == RiskLevel.LOW:
            return {"allowed": True, "risk_level": risk.value}

        if risk == RiskLevel.CRITICAL:
            return {
                "allowed": False,
                "risk_level": risk.value,
                "reason": "Critical tools are denied by default. Enable in settings.",
            }

        # Check if already approved for this session
        if risk == RiskLevel.MEDIUM:
            approved = self._approved_tools.get(session_id, set())
            if tool_name in approved:
                return {"allowed": True, "risk_level": risk.value}

        # Need approval
        request_id = str(uuid.uuid4())
        self._pending_requests[request_id] = {
            "session_id": session_id,
            "tool_name": tool_name,
            "risk_level": risk.value,
            "params": params or {},
        }
        return {
            "allowed": False,
            "risk_level": risk.value,
            "request_id": request_id,
            "reason": "Approval required",
        }

    def approve(self, request_id: str) -> dict | None:
        """Approve a pending tool request.

        Returns the pending request info or None if not found.
        Stores approved params for resume of HIGH risk tools.
        """
        pending = self._pending_requests.pop(request_id, None)
        if pending is None:
            return None

        session_id = pending["session_id"]
        tool_name = pending["tool_name"]
        risk = RiskLevel(pending["risk_level"])

        # Remember for medium risk
        if risk == RiskLevel.MEDIUM:
            if session_id not in self._approved_tools:
                self._approved_tools[session_id] = set()
            self._approved_tools[session_id].add(tool_name)

        # Store for resume (all risk levels)
        self._approved_params[request_id] = pending

        return pending

    def deny(self, request_id: str) -> dict | None:
        """Deny a pending tool request.

        Returns the pending request info or None if not found.
        """
        return self._pending_requests.pop(request_id, None)

    def get_pending(self, request_id: str) -> dict | None:
        """Get pending request info without resolving it."""
        return self._pending_requests.get(request_id)

    def clear_session(self, session_id: str) -> None:
        """Clear remembered approvals for a session."""
        self._approved_tools.pop(session_id, None)
        # Also clear pending requests for this session
        to_remove = [
            rid for rid, p in self._pending_requests.items()
            if p["session_id"] == session_id
        ]
        for rid in to_remove:
            self._pending_requests.pop(rid, None)

    def get_approved_params(self, request_id: str) -> dict | None:
        """Get approved tool params for resume. Returns None if not approved."""
        return self._approved_params.get(request_id)

    def consume_approved_params(self, request_id: str) -> dict | None:
        """Get and remove approved tool params (one-shot resume)."""
        return self._approved_params.pop(request_id, None)


# Global gate instance
permission_gate = PermissionGate()


def write_audit_log(
    session_id: str,
    tool_name: str,
    risk_level: str,
    action: str,
    params: dict | None = None,
    result: str = "",
) -> dict:
    """Write an audit log entry."""
    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        params_json = json.dumps(params or {}, ensure_ascii=False)
        cursor = conn.execute(
            "INSERT INTO audit_log (session_id, tool_name, risk_level, action, params, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, tool_name, risk_level, action, params_json, result, now),
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "session_id": session_id,
            "tool_name": tool_name,
            "risk_level": risk_level,
            "action": action,
            "params": params or {},
            "result": result,
            "created_at": now,
        }
    finally:
        conn.close()


def get_audit_logs(session_id: str, limit: int = 50) -> list[dict]:
    """Get audit logs for a session."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, session_id, tool_name, risk_level, action, params, result, created_at "
            "FROM audit_log WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "tool_name": row["tool_name"],
                "risk_level": row["risk_level"],
                "action": row["action"],
                "params": json.loads(row["params"]),
                "result": row["result"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()
