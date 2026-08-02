"""Shared types for the Python hand-written Agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Type

from pydantic import BaseModel


AgentEventType = Literal[
    "session_start",
    "tool_call",
    "tool_result",
    "text_delta",
    "done",
    "permission_required",
    "run_complete",
    "error",
    "model_decision",
    "final_response",
]


@dataclass
class ToolCall:
    """A planned tool call inside the Python loop."""

    name: str
    params: dict[str, Any]


@dataclass
class ToolResult:
    """Normalized tool result."""

    success: bool
    output: Any
    is_error: bool = False


@dataclass
class ToolDefinition:
    """Registered Python tool."""

    name: str
    description: str
    parameters: dict[str, Any]
    risk_level: str
    execute: Callable[[dict[str, Any]], Any]
    validator: Type[BaseModel] | None = None


@dataclass
class AgentConfig:
    """Runtime limits and switches."""

    max_iterations: int = 4
    max_tool_calls: int = 8
    max_output_chars: int = 5000
    total_timeout_seconds: float = 45.0


@dataclass
class AgentResult:
    """Final result returned by the Python Agent loop."""

    session_id: str
    trace_id: str
    success: bool
    final_output: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    waiting_approval: bool = False
    error: str = ""
