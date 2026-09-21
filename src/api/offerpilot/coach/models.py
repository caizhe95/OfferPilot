"""Shared types for the Python hand-written Agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Type

from pydantic import BaseModel


AgentEventType = Literal[
    "tool_call",
    "tool_result",
    "text_delta",
    "error",
    "model_decision",
    "final_response",
    "approval_required",
]


@dataclass
class ToolDefinition:
    """Registered Python tool."""

    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[[dict[str, Any]], Any]
    validator: Type[BaseModel] | None = None


@dataclass
class AgentResult:
    """Final result returned by the Python Agent loop."""

    session_id: str
    run_id: str
    success: bool
    final_output: str = ""
    waiting_approval: bool = False
    error: str = ""
