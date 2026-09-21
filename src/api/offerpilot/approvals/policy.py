"""Risk classification for the restricted tool surface."""

from __future__ import annotations

from enum import Enum

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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
    return TOOL_RISK_MAP.get(tool_name, RiskLevel.HIGH)
