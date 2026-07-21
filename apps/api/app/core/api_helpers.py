"""Shared helpers for API endpoints that execute guarded tools."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

from app.harness.harness import HarnessRunner
from app.permission.permission import build_permission_required_event, permission_gate, write_audit_log
from app.session.session import add_progress_event, mark_waiting_approval
from app.trace.trace_eval import add_trace_event


def permission_required_response(
    *,
    session_id: str,
    tool_name: str,
    permission_result: dict,
    params: dict | None = None,
    trace_id: str | None = None,
    message: str | None = None,
) -> dict:
    """Build canonical permission_required payload and update audit/session state."""
    event = build_permission_required_event(
        session_id=session_id,
        tool_name=tool_name,
        permission_result=permission_result,
        params=params,
        message=message,
    )
    write_audit_log(
        session_id=session_id,
        tool_name=tool_name,
        risk_level=event["risk_level"],
        action="request",
        params=params,
    )
    try:
        mark_waiting_approval(session_id, event["request_id"], tool_name)
    except Exception as exc:
        event["message"] = f"{event['message']}; session state not changed: {exc}"
    if trace_id:
        add_trace_event(trace_id, "permission_required", 0, event)
    return event


def prepare_tool_runner(
    session_id: str,
    tool_name: str,
    params: dict,
    trace_id: str | None = None,
) -> HarnessRunner:
    """Run pre-tool checks and return a configured HarnessRunner."""
    runner = HarnessRunner(session_id=session_id)
    try:
        checked = runner.pre_tool(tool_name, params)
        if trace_id:
            add_trace_event(trace_id, "pre_tool", 0, {"tool_name": tool_name, "budget": runner.budget.get_status()})
        # Hooks may return the original params object; copy before clearing it.
        checked_params = dict(checked)
        params.clear()
        params.update(checked_params)
        return runner
    except Exception as exc:
        if trace_id:
            add_trace_event(trace_id, "pre_tool", 0, {"tool_name": tool_name, "error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))


def finalize_tool_result(
    runner: HarnessRunner,
    tool_name: str,
    result: Any,
    trace_id: str | None = None,
) -> Any:
    """Run post-tool normalization and emit trace metadata."""
    normalized = runner.post_tool(tool_name, result)
    if trace_id:
        add_trace_event(trace_id, "post_tool", 0, {"tool_name": tool_name, "budget": runner.budget.get_status()})
    return normalized


def record_stage(
    *,
    session_id: str,
    trace_id: str | None,
    stage: str,
    metadata: dict | None = None,
    trace_event_type: str | None = None,
    step_index: int | None = None,
    trace_data: dict | None = None,
) -> None:
    """Record one progress stage and optional matching trace event."""
    add_progress_event(session_id, stage, metadata)
    if trace_id and trace_event_type:
        add_trace_event(trace_id, trace_event_type, step_index, trace_data or {})


def run_guarded_tool(
    *,
    session_id: str,
    tool_name: str,
    params: dict,
    trace_id: str | None,
    execute: Callable[[], Any],
    trace_payload: Callable[[Any], dict] | None = None,
) -> Any:
    """Convenience wrapper for the common guarded-tool flow."""
    runner = prepare_tool_runner(session_id, tool_name, params, trace_id)
    permission = permission_gate.check(session_id, tool_name, params)
    if not permission.get("allowed"):
        return permission_required_response(
            session_id=session_id,
            tool_name=tool_name,
            permission_result=permission,
            params=params,
            trace_id=trace_id,
        )

    result = execute()
    if trace_id and trace_payload is not None:
        add_trace_event(trace_id, "tool_call", 0, trace_payload(result))
    return finalize_tool_result(runner, tool_name, result, trace_id)
