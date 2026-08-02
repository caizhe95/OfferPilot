"""Permission gate and audit system.

Implements tool risk classification, permission checks, approval flow,
and audit logging.
"""

import json
from datetime import datetime, timezone
from enum import Enum
from offerpilot.core.database import get_db


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
    """Stateless risk policy. Durable approvals live in ``coaching.state``."""

    def check(
        self,
        session_id: str,
        tool_name: str,
        params: dict | None = None,
        *,
        profile_id: str | None = None,
    ) -> dict:
        risk = get_tool_risk(tool_name)
        if risk == RiskLevel.LOW:
            return {"allowed": True, "risk_level": risk.value}
        if risk == RiskLevel.CRITICAL:
            return {
                "allowed": False,
                "risk_level": risk.value,
                "reason": "Critical tools are denied by default. Enable in settings.",
            }
        if risk == RiskLevel.MEDIUM and profile_id:
            from offerpilot.coaching.state import has_permission_grant

            if has_permission_grant(session_id, profile_id, tool_name):
                return {"allowed": True, "risk_level": risk.value}
        return {"allowed": False, "risk_level": risk.value, "reason": "Approval required"}


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
