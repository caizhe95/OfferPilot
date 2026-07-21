"""Durable native tool-calling loop for the restricted Coach Agent."""

from __future__ import annotations

import json
import time
from typing import Any

from app.agent.registry import ToolRegistry, create_coach_registry
from app.agent.types import AgentConfig, AgentResult
from app.coaching.state import create_approval, get_run, save_run
from app.llm.llm_client import tool_chat_completion
from app.permission.permission import get_tool_risk, write_audit_log
from app.session.session import add_message, get_recent_messages, mark_waiting_approval
from app.trace.trace_eval import add_trace_event, complete_trace

SYSTEM_PROMPT = """You are OfferPilot Lite's restricted technical interview coach. You may only diagnose, practice, review, ask interview follow-ups, and recommend questions using the listed tools. You are not an open-domain assistant: reject resumes, job descriptions, and general job-search requests. Use tools when needed, otherwise answer in concise Simplified Chinese."""
HISTORY_MESSAGES = 12
HISTORY_CHARS = 18000


class CoachLoop:
    def __init__(self, *, session_id: str, profile_id: str, trace_id: str, config: AgentConfig | None = None) -> None:
        self.session_id, self.profile_id, self.trace_id = session_id, profile_id, trace_id
        self.config = config or AgentConfig()
        self.events: list[dict[str, Any]] = []

    async def run(self) -> AgentResult:
        history = _bounded_history(get_recent_messages(self.session_id, n=HISTORY_MESSAGES))
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
        self._emit("session_start", session_id=self.session_id, trace_id=self.trace_id)
        return self._continue(messages, iteration=0, tool_calls=0, elapsed=0.0)

    async def resume(self, request_id: str, approval: dict[str, Any]) -> AgentResult:
        saved = get_run(self.session_id, self.profile_id)
        if saved is None or saved["status"] != "waiting_approval":
            return self._finish("", "missing_resume_state", success=False, error="Coach resume state not found")
        state = saved["state"]
        self.trace_id = saved["trace_id"]
        if state.get("request_id") != request_id:
            return self._finish("", "invalid_resume_request", success=False, error="Approval does not match Coach run")
        messages = state["messages"]
        pending = state["pending_call"]
        if approval["status"] == "approved":
            registry = self._registry(lambda: max(0.1, self.config.total_timeout_seconds - float(state.get("elapsed", 0))))
            try:
                result = registry.execute(pending["name"], pending["params"])
                write_audit_log(self.session_id, pending["name"], approval["risk_level"], "execute", pending["params"], str(result)[:500])
            except Exception as exc:
                result = {"error": "tool_execution_failed", "message": str(exc)}
        else:
            result = {"error": "permission_denied", "tool_name": pending["name"]}
        messages.append({"role": "tool", "tool_call_id": pending["id"], "content": json.dumps(result, ensure_ascii=False)})
        self._emit("tool_result", tool_name=pending["name"], result=result)
        return self._continue(messages, iteration=int(state["iteration"]), tool_calls=int(state["tool_calls"]) + 1, elapsed=float(state.get("elapsed", 0)))

    def _continue(self, messages: list[dict[str, Any]], *, iteration: int, tool_calls: int, elapsed: float) -> AgentResult:
        active_started = time.monotonic()
        try:
            while iteration < self.config.max_iterations:
                remaining = self.config.total_timeout_seconds - elapsed - (time.monotonic() - active_started)
                if remaining <= 0:
                    return self._finish("", "timeout", success=False)
                registry = self._registry(lambda: max(0.1, self.config.total_timeout_seconds - elapsed - (time.monotonic() - active_started)))
                reply = tool_chat_completion(messages=messages, tools=registry.openai_schemas(), timeout=remaining)
                iteration += 1
                names = [call["name"] for call in reply.tool_calls]
                self._emit("model_decision", iteration=iteration, content=reply.content, tool_calls=names)
                add_trace_event(self.trace_id, "model_decision", iteration, {"tool_calls": names})
                if not reply.tool_calls:
                    return self._finish(reply.content, "final_response", success=True)
                messages.append({"role": "assistant", "content": reply.content or None, "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in reply.tool_calls]})
                for call in reply.tool_calls:
                    if tool_calls >= self.config.max_tool_calls:
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"error": "tool_budget_exceeded"})})
                        continue
                    result, paused = self._execute(registry, call, iteration)
                    if paused:
                        elapsed_now = elapsed + time.monotonic() - active_started
                        state = {"messages": messages, "iteration": iteration, "tool_calls": tool_calls, "elapsed": elapsed_now, "request_id": result["request_id"], "pending_call": result["pending_call"]}
                        save_run(self.session_id, self.profile_id, self.trace_id, "waiting_approval", state)
                        mark_waiting_approval(self.session_id, result["request_id"], call["name"])
                        complete_trace(self.trace_id, "waiting_approval")
                        self._emit({"type": "permission_required", "session_id": self.session_id, "request_id": result["request_id"], "tool_name": call["name"], "risk_level": get_tool_risk(call["name"]).value, "params": result["pending_call"]["params"], "message": "Tool call requires user approval"})
                        return AgentResult(self.session_id, self.trace_id, True, events=self.events, waiting_approval=True)
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)})
                    tool_calls += 1
            return self._finish("本轮工具调用已达到上限，请根据已有结果继续练习。", "iteration_limit", success=True)
        except Exception as exc:
            return self._finish("", f"error: {exc}", success=False, error=str(exc))

    def _execute(self, registry: ToolRegistry, call: dict[str, str], iteration: int) -> tuple[Any, bool]:
        name = call["name"]
        try:
            raw = json.loads(call["arguments"])
            if not isinstance(raw, dict):
                raise ValueError("arguments must be an object")
            params = registry.validate(name, raw)
        except Exception as exc:
            result = {"error": "invalid_tool_arguments", "message": str(exc)}
            self._emit("tool_result", tool_name=name, result=result)
            return result, False
        self._emit("tool_call", tool_name=name, params=params)
        add_trace_event(self.trace_id, "tool_call", iteration, {"tool_name": name})
        risk = get_tool_risk(name)
        if risk.value in {"high", "medium"}:
            request_id = create_approval(self.session_id, self.profile_id, name, risk.value, params)
            write_audit_log(self.session_id, name, risk.value, "request", params)
            return {"request_id": request_id, "pending_call": {"id": call["id"], "name": name, "params": params}}, True
        result = registry.execute(name, params)
        self._emit("tool_result", tool_name=name, result=result)
        add_trace_event(self.trace_id, "tool_result", iteration, {"tool_name": name})
        return result, False

    def _registry(self, remaining_timeout: Any) -> ToolRegistry:
        return create_coach_registry(self.session_id, self.profile_id, self.trace_id, remaining_timeout)

    def _finish(self, content: str, reason: str, *, success: bool, error: str = "") -> AgentResult:
        if content:
            add_message(self.session_id, "assistant", content)
            self._emit("text_delta", content=content)
        save_run(self.session_id, self.profile_id, self.trace_id, "completed" if success else "failed", {})
        complete_trace(self.trace_id, "completed" if success else "failed")
        self._emit("final_response", content=content, termination_reason=reason)
        self._emit("run_complete", session_id=self.session_id, trace_id=self.trace_id, success=success, error=error)
        return AgentResult(self.session_id, self.trace_id, success, final_output=content, events=self.events, error=error)

    def _emit(self, event_type: str | dict[str, Any], **data: Any) -> None:
        self.events.append(event_type if isinstance(event_type, dict) else {"type": event_type, **data})


def _bounded_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    used = 0
    for message in reversed(messages[-HISTORY_MESSAGES:]):
        content = str(message.get("content", ""))
        if used + len(content) > HISTORY_CHARS:
            break
        selected.append({"role": message["role"], "content": content})
        used += len(content)
    return list(reversed(selected))
