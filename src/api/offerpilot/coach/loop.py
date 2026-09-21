"""Durable, asynchronous native tool-calling loop for the restricted Coach."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, AsyncGenerator, AsyncIterator, Awaitable

from offerpilot.coach.context import (
    HISTORY_MESSAGES,
    READ_ONLY_TOOL_NAMES,
    SYSTEM_PROMPT,
    bounded_history,
    render_read_only_tool_result,
    tool_context,
    tool_names_for_turn,
    tool_result_has_error,
)
from offerpilot.coach.models import AgentResult
from offerpilot.coach.tools import ToolRegistry, create_coach_registry
from offerpilot.core.deadlines import await_with_deadline, deadline_after, raise_if_cancelled, remaining_seconds
from offerpilot.llm.chat import ToolChatResult, tool_chat_completion
from offerpilot.approvals.policy import get_tool_risk
from offerpilot.approvals.repository import create_approval
from offerpilot.approvals.service import claim_approval, finish_approval
from offerpilot.runs.repository import get_run, save_run_state
from offerpilot.runs.operation_logs import write_permission_log
from offerpilot.sessions.repository import add_message, get_messages
from offerpilot.database.values import now
from offerpilot.runs.calls import begin_call, finish_call, record_attempt

MAX_ITERATIONS = 4
MAX_TOOL_CALLS = 8
MAX_OUTPUT_CHARS = 5000
COACH_TIMEOUT_SECONDS = 45.0

def _failure_code(exc: Exception) -> str:
    category = getattr(exc, "category", "")
    if isinstance(category, str) and category:
        return category
    code = getattr(exc, "code", "")
    if isinstance(code, str) and code:
        return code
    return exc.__class__.__name__.lower()


class CoachLoop:
    """A bounded Coach run that can be consumed as a real-time event stream."""

    def __init__(
        self,
        *,
        session_id: str,
        profile_id: str,
        run_id: str,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        self.session_id = session_id
        self.profile_id = profile_id
        self.run_id = run_id
        self.cancel_event = cancel_event or asyncio.Event()
        self.events: list[dict[str, Any]] = []
        self.result: AgentResult | None = None
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._consumer_open = False
        self._sequence = 0
        self._deadline: float | None = None
        self._output_chars = 0
        self._streamed_output: list[str] = []

    async def stream(self, *, heartbeat_seconds: float = 15.0) -> AsyncIterator[dict[str, Any]]:
        """Yield events while the model is still producing the current turn."""
        events = self._stream_task(self.run(), heartbeat_seconds=heartbeat_seconds)
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()

    async def resume_stream(
        self,
        approval_id: str,
        approval: dict[str, Any],
        *,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Resume a paused Coach run without buffering its later model output."""
        events = self._stream_task(self.resume(approval_id, approval), heartbeat_seconds=heartbeat_seconds)
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()

    async def _stream_task(
        self,
        operation: Awaitable[AgentResult],
        *,
        heartbeat_seconds: float,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Bridge a running Coach coroutine to a bounded SSE event queue."""
        # This queue is internal to the worker bridge, not a client buffer.  It
        # must never discard a terminal or approval event; durable Run events
        # remain the source of truth for browser reconnects.
        self._queue = asyncio.Queue()
        self._consumer_open = True
        task: asyncio.Future[AgentResult] = asyncio.ensure_future(operation)
        try:
            while True:
                if task.done() and self._queue.empty():
                    break
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=max(0.01, heartbeat_seconds))
                except TimeoutError:
                    self._sequence += 1
                    yield {
                        "type": "ping",
                        "session_id": self.session_id,
                        "trace_id": self.run_id,
                        "run_id": self.run_id,
                        "sequence": self._sequence,
                    }
                    continue
                yield item
        finally:
            self._consumer_open = False
            if not task.done():
                self.cancel_event.set()
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def run(self) -> AgentResult:
        try:
            self._deadline = deadline_after(COACH_TIMEOUT_SECONDS)
            history = bounded_history(
                get_messages(self.session_id, n=HISTORY_MESSAGES, profile_id=self.profile_id)
            )
            messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
            self.result = await self._continue(messages, iteration=0, tool_calls=0)
            return self.result
        except asyncio.CancelledError:
            self.result = await self._finish("", "cancelled", success=False, cancelled=True)
            return self.result
        except Exception as exc:
            self.result = await self._finish("", "run_error", success=False, error=_failure_code(exc))
            return self.result
    async def resume(self, approval_id: str, approval: dict[str, Any]) -> AgentResult:
        try:
            saved = get_run(self.run_id, self.profile_id)
            if saved is None or saved["status"] not in {"waiting_approval", "running"}:
                self.result = await self._finish("", "missing_resume_state", success=False, error="Coach resume state not found")
                return self.result
            state = saved["state"]
            self._sequence = int(state.get("sequence", 0))
            self._deadline = deadline_after(
                max(0.001, COACH_TIMEOUT_SECONDS - float(state.get("elapsed", 0)))
            )
            if state.get("approval_id") != approval_id:
                self.result = await self._finish("", "invalid_resume_request", success=False, error="Approval does not match Coach run")
                return self.result
            messages = state["messages"]
            pending = state["pending_call"]
            if approval["status"] == "approved":
                claimed = claim_approval(approval_id, self.profile_id)
                if claimed is None:
                    self.result = await self._finish("", "approval_already_consumed", success=False, error="Approval is not available for execution")
                    return self.result
                try:
                    registry = self._registry()
                    result = await self._execute_tool(
                        registry,
                        pending["name"],
                        pending["params"],
                        logical_call_id=f"tool:approved:{pending.get('id') or pending['name']}",
                    )
                    finish_approval(approval_id, True)
                    write_permission_log(
                        self.session_id,
                        pending["name"],
                        approval["risk_level"],
                        "execute",
                        result="ok",
                        profile_id=self.profile_id,
                        run_id=self.run_id,
                    )
                except asyncio.CancelledError:
                    finish_approval(approval_id, False, "cancelled")
                    raise
                except Exception as exc:
                    finish_approval(approval_id, False, _failure_code(exc))
                    write_permission_log(
                        self.session_id,
                        pending["name"],
                        approval["risk_level"],
                        "execute",
                        result="tool_execution_failed",
                        profile_id=self.profile_id,
                        run_id=self.run_id,
                    )
                    result = {"error": "tool_execution_failed"}
            else:
                result = {"error": "permission_denied", "tool_name": pending["name"]}
            messages.append({"role": "tool", "tool_call_id": pending["id"], "content": tool_context(result)})
            await self._emit("tool_result", tool_name=pending["name"], result=result)
            self.result = await self._continue(
                messages,
                iteration=int(state["iteration"]),
                tool_calls=int(state["tool_calls"]) + 1,
            )
            return self.result
        except asyncio.CancelledError:
            self.result = await self._finish("", "cancelled", success=False, cancelled=True)
            return self.result
        except Exception as exc:
            self.result = await self._finish("", "resume_error", success=False, error=str(exc))
            return self.result
    async def _continue(self, messages: list[dict[str, Any]], *, iteration: int, tool_calls: int) -> AgentResult:
        try:
            while iteration < MAX_ITERATIONS:
                self._raise_if_cancelled()
                remaining = self._remaining()
                if remaining <= 0:
                    return await self._finish("", "timeout", success=False, error="active run budget exhausted")
                registry = self._registry()
                selected_tool_names = tool_names_for_turn(messages)
                tool_schemas = [
                    schema for schema in registry.openai_schemas()
                    if schema["function"]["name"] in selected_tool_names
                ]
                tool_choice: str | dict[str, Any] = "auto"
                if len(tool_schemas) == 1:
                    tool_choice = "required"
                provider_reply: ToolChatResult | Awaitable[ToolChatResult] = tool_chat_completion(
                    messages=messages,
                    tools=tool_schemas,
                    tool_choice=tool_choice,
                    timeout=remaining,
                    cancel_event=self.cancel_event,
                    deadline=self._deadline,
                    run_id=self.run_id,
                    session_id=self.session_id,
                    profile_id=self.profile_id,
                    logical_call_id=f"coach:chat:{iteration + 1}",
                )
                reply = await _resolve_tool_reply(provider_reply)
                iteration += 1
                names = [call["name"] for call in reply.tool_calls]
                await self._emit("model_decision", iteration=iteration, tool_calls=names)
                if not reply.tool_calls:
                    return await self._finish(reply.content, "final_response", success=True)
                messages.append({
                    "role": "assistant",
                    "content": reply.content or None,
                    "tool_calls": [
                        {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"]}}
                        for call in reply.tool_calls
                    ],
                })
                for call in reply.tool_calls:
                    self._raise_if_cancelled()
                    if tool_calls >= MAX_TOOL_CALLS:
                        result = {"error": "tool_budget_exceeded"}
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": tool_context(result)})
                        await self._emit("tool_result", tool_name=call["name"], result=result)
                        continue
                    result, paused = await self._execute(registry, call, iteration)
                    if paused:
                        state = {
                            "messages": messages,
                            "iteration": iteration,
                            "tool_calls": tool_calls,
                            "elapsed": self._elapsed(),
                            "sequence": self._sequence,
                            "approval_id": result["approval_id"],
                            "pending_call": result["pending_call"],
                        }
                        if save_run_state(self.run_id, state) is None:
                            raise asyncio.CancelledError()
                        await self._emit(
                            "approval_required",
                            approval_id=result["approval_id"],
                            tool_name=call["name"],
                            risk_level=get_tool_risk(call["name"]).value,
                            public_params=result["public_params"],
                            flow_kind="coach",
                            message="Tool call requires user approval",
                        )
                        return AgentResult(self.session_id, self.run_id, True, waiting_approval=True)
                    if call["name"] in READ_ONLY_TOOL_NAMES and not tool_result_has_error(result):
                        return await self._finish(
                            render_read_only_tool_result(call["name"], result),
                            "tool_result_complete",
                            success=True,
                        )
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": tool_context(result)})
                    tool_calls += 1
            return await self._finish("本轮工具调用已达到上限，请根据已有结果继续练习。", "iteration_limit", success=True)
        except asyncio.CancelledError:
            return await self._finish("", "cancelled", success=False, cancelled=True)
        except Exception as exc:
            return await self._finish("", "provider_or_tool_error", success=False, error=_failure_code(exc))

    async def _execute(self, registry: ToolRegistry, call: dict[str, str], iteration: int) -> tuple[Any, bool]:
        name = call["name"]
        try:
            raw = json.loads(call["arguments"])
            if not isinstance(raw, dict):
                raise ValueError("arguments must be an object")
            params = registry.validate(name, raw)
        except Exception as exc:
            result = {"error": "invalid_tool_arguments", "message": str(exc)}
            await self._emit("tool_result", tool_name=name, result=result)
            return result, False
        await self._emit("tool_call", tool_name=name, params=params)
        risk = get_tool_risk(name)
        if risk.value in {"high", "medium"}:
            public_params = {"key": params.get("key", ""), "category": params.get("category", "general")}
            approval_id = create_approval(
                self.run_id,
                self.session_id,
                self.profile_id,
                name,
                risk.value,
                params,
                flow_kind="coach",
                public_params=public_params,
            )
            write_permission_log(
                self.session_id,
                name,
                risk.value,
                "request",
                profile_id=self.profile_id,
                run_id=self.run_id,
            )
            return {
                "approval_id": approval_id,
                "pending_call": {"id": call["id"], "name": name, "params": params},
                "public_params": public_params,
            }, True
        result = await self._execute_tool(
            registry,
            name,
            params,
            logical_call_id=f"tool:{iteration}:{call['id'] or name}",
        )
        await self._emit("tool_result", tool_name=name, result=result)
        return result, False

    async def _execute_tool(
        self,
        registry: ToolRegistry,
        name: str,
        params: dict[str, Any],
        *,
        logical_call_id: str,
    ) -> Any:
        self._raise_if_cancelled()
        tool = registry.get(name)
        if tool is None:
            raise ValueError("unknown_tool")
        started_at = now()
        begin_call(
            run_id=self.run_id,
            session_id=self.session_id,
            profile_id=self.profile_id,
            logical_call_id=logical_call_id,
            call_type="tool",
            operation_name=name,
        )
        record_attempt(self.run_id, self.profile_id, logical_call_id, 1, 0)
        try:
            result = await await_with_deadline(
                asyncio.to_thread(tool.execute, params),
                deadline=self._deadline,
                cancel_event=self.cancel_event,
            )
            if inspect.isawaitable(result):
                result = await await_with_deadline(
                    result, deadline=self._deadline, cancel_event=self.cancel_event
                )
        except asyncio.CancelledError:
            finish_call(run_id=self.run_id, profile_id=self.profile_id, logical_call_id=logical_call_id, status="cancelled", started_at=started_at, error_category="cancelled")
            raise
        except Exception as exc:
            finish_call(run_id=self.run_id, profile_id=self.profile_id, logical_call_id=logical_call_id, status="failed", started_at=started_at, error_category=exc.__class__.__name__.lower())
            raise
        finish_call(run_id=self.run_id, profile_id=self.profile_id, logical_call_id=logical_call_id, status="failed" if tool_result_has_error(result) else "succeeded", started_at=started_at, error_category="tool_error" if tool_result_has_error(result) else "")
        return result

    def _registry(self) -> ToolRegistry:
        return create_coach_registry(
            self.session_id,
            self.profile_id,
            self.run_id,
            deadline=self._deadline,
            cancel_event=self.cancel_event,
        )

    async def _finish(
        self,
        content: str,
        reason: str,
        *,
        success: bool,
        error: str = "",
        cancelled: bool = False,
        already_streamed: bool = False,
    ) -> AgentResult:
        if already_streamed and self._streamed_output:
            content = "".join(self._streamed_output)
        elif content:
            content = content[: max(0, MAX_OUTPUT_CHARS - self._output_chars)]
        if content:
            add_message(
                self.session_id,
                "assistant",
                content,
                run_id=self.run_id,
                kind="coach_response",
                profile_id=self.profile_id,
            )
            if not already_streamed:
                await self._emit("text_delta", content=content)
        await self._emit("final_response", content=content, termination_reason=reason)
        return AgentResult(self.session_id, self.run_id, success, final_output=content, error=error)

    async def _emit(self, event_type: str, **data: Any) -> None:
        if event_type == "text_delta":
            content = self._truncate_output(str(data.get("content", "")))
            if not content:
                return
            data["content"] = content
            self._streamed_output.append(content)
        self._sequence += 1
        event = {
            "type": event_type,
            "session_id": self.session_id,
            "trace_id": self.run_id,
            "run_id": self.run_id,
            "sequence": self._sequence,
            **data,
        }
        self.events.append(event)
        if self._queue is not None and self._consumer_open:
            self._queue.put_nowait(event)

    def _raise_if_cancelled(self) -> None:
        raise_if_cancelled(self.cancel_event)

    def _remaining(self) -> float:
        return remaining_seconds(self._deadline)

    def _elapsed(self) -> float:
        return max(0.0, COACH_TIMEOUT_SECONDS - max(0.0, self._remaining()))

    def _truncate_output(self, content: str) -> str:
        remaining = MAX_OUTPUT_CHARS - self._output_chars
        if remaining <= 0:
            return ""
        bounded = content[:remaining]
        self._output_chars += len(bounded)
        return bounded


async def _resolve_tool_reply(value: ToolChatResult | Awaitable[ToolChatResult]) -> ToolChatResult:
    """Keep synchronous test doubles compatible with the asynchronous provider contract."""
    if inspect.isawaitable(value):
        return await value
    return value
