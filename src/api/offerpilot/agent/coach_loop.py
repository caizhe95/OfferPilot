"""Durable, asynchronous native tool-calling loop for the restricted Coach."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, AsyncGenerator, AsyncIterator, Awaitable

from offerpilot.agent.registry import ToolRegistry, create_coach_registry
from offerpilot.agent.types import AgentConfig, AgentResult
from offerpilot.coaching.state import claim_approved_approval, create_approval, finish_approval, get_run, save_run
from offerpilot.core.deadline import await_with_deadline, deadline_after, raise_if_cancelled, remaining_seconds
from offerpilot.llm.llm_client import ToolChatResult, tool_chat_completion
from offerpilot.permission.permission import get_tool_risk, write_audit_log
from offerpilot.session.session import add_message, get_recent_messages, mark_waiting_approval
from offerpilot.trace.trace_eval import add_trace_event, complete_trace

SYSTEM_PROMPT = """You are OfferPilot Lite's restricted technical interview coach. You may only practice, review, ask interview follow-ups, and recommend questions using the listed tools. Formal scoring is available only through the explicit diagnosis mode, never through this conversation. You are not an open-domain assistant: reject resumes, job descriptions, and general job-search requests. When the user asks for knowledge lookup, question recommendation, historical reports, or approved memory, you MUST call the matching listed tool before answering; never claim a retrieved result without its tool result. Otherwise answer in concise Simplified Chinese."""
HISTORY_MESSAGES = 12
_READ_ONLY_TOOL_NAMES = {
    "search_knowledge",
    "recommend_next_question",
    "get_practice_profile",
    "list_recent_reports",
}


def _failure_code(exc: Exception) -> str:
    category = getattr(exc, "category", "")
    if isinstance(category, str) and category:
        return category
    code = getattr(exc, "code", "")
    if isinstance(code, str) and code:
        return code
    return exc.__class__.__name__.lower()


def _tool_names_for_turn(messages: list[dict[str, Any]]) -> set[str]:
    """Expose only the tool surface relevant to the latest user turn."""
    if messages and messages[-1].get("role") == "tool":
        return set()
    latest_user_text = next(
        (str(message.get("content", "")).lower() for message in reversed(messages) if message.get("role") == "user"),
        "",
    )
    if any(token in latest_user_text for token in ("save memory", "remember this", "保存记忆", "记住这个")):
        return {"save_memory"}
    if any(token in latest_user_text for token in ("recent report", "history report", "历史报告", "最近报告")):
        return {"list_recent_reports"}
    if any(token in latest_user_text for token in ("my memory", "practice profile", "我的记忆", "练习偏好")):
        return {"get_practice_profile"}
    if any(token in latest_user_text for token in ("recommend", "推荐")):
        return {"recommend_next_question"}
    if any(token in latest_user_text for token in ("search", "knowledge", "lookup", "检索", "题库", "知识库")):
        return {"search_knowledge"}
    return set()
HISTORY_CHARS = 18000
MAX_TOOL_CONTEXT_CHARS = 12000


class CoachLoop:
    """A bounded Coach run that can be consumed as a real-time event stream."""

    def __init__(
        self,
        *,
        session_id: str,
        profile_id: str,
        trace_id: str,
        config: AgentConfig | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        self.session_id = session_id
        self.profile_id = profile_id
        self.trace_id = trace_id
        self.config = config or AgentConfig()
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
        request_id: str,
        approval: dict[str, Any],
        *,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Resume a paused Coach run without buffering its later model output."""
        events = self._stream_task(self.resume(request_id, approval), heartbeat_seconds=heartbeat_seconds)
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
        self._queue = asyncio.Queue(maxsize=128)
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
                        "trace_id": self.trace_id,
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
            self._deadline = deadline_after(self.config.total_timeout_seconds)
            history = _bounded_history(get_recent_messages(self.session_id, n=HISTORY_MESSAGES))
            messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
            await self._emit("session_start", mode="coach")
            self.result = await self._continue(messages, iteration=0, tool_calls=0)
            return self.result
        except asyncio.CancelledError:
            self.result = await self._finish("", "cancelled", success=False, cancelled=True)
            return self.result
        except Exception as exc:
            self.result = await self._finish("", "run_error", success=False, error=_failure_code(exc))
            return self.result
    async def resume(self, request_id: str, approval: dict[str, Any]) -> AgentResult:
        try:
            saved = get_run(self.session_id, self.profile_id)
            if saved is None or saved["status"] not in {"waiting_approval", "running"}:
                self.result = await self._finish("", "missing_resume_state", success=False, error="Coach resume state not found")
                return self.result
            state = saved["state"]
            self.trace_id = saved["trace_id"]
            self._sequence = int(state.get("sequence", 0))
            self._deadline = deadline_after(
                max(0.001, self.config.total_timeout_seconds - float(state.get("elapsed", 0)))
            )
            if state.get("request_id") != request_id:
                self.result = await self._finish("", "invalid_resume_request", success=False, error="Approval does not match Coach run")
                return self.result
            messages = state["messages"]
            pending = state["pending_call"]
            save_run(self.session_id, self.profile_id, self.trace_id, "running", state, run_kind="coach")
            if approval["status"] == "approved":
                claimed = claim_approved_approval(request_id, self.session_id, self.profile_id)
                if claimed is None:
                    self.result = await self._finish("", "approval_already_consumed", success=False, error="Approval is not available for execution")
                    return self.result
                try:
                    registry = self._registry()
                    result = await self._execute_tool(registry, pending["name"], pending["params"])
                    finish_approval(request_id, succeeded=True)
                    write_audit_log(self.session_id, pending["name"], approval["risk_level"], "execute", pending["params"], str(result)[:500])
                except asyncio.CancelledError:
                    finish_approval(request_id, succeeded=False, error="cancelled")
                    raise
                except Exception as exc:
                    finish_approval(request_id, succeeded=False, error=str(exc))
                    result = {"error": "tool_execution_failed", "message": str(exc)}
            else:
                result = {"error": "permission_denied", "tool_name": pending["name"]}
            messages.append({"role": "tool", "tool_call_id": pending["id"], "content": _tool_context(result)})
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
            while iteration < self.config.max_iterations:
                self._raise_if_cancelled()
                remaining = self._remaining()
                if remaining <= 0:
                    return await self._finish("", "timeout", success=False, error="active run budget exhausted")
                registry = self._registry()
                selected_tool_names = _tool_names_for_turn(messages)
                tool_schemas = [
                    schema for schema in registry.openai_schemas()
                    if schema["function"]["name"] in selected_tool_names
                ]
                tool_choice: str | dict[str, Any] = "auto"
                if len(tool_schemas) == 1:
                    tool_choice = {"type": "function", "function": {"name": tool_schemas[0]["function"]["name"]}}
                stream_response = not (messages and messages[-1].get("role") == "tool")
                received_delta = False

                async def on_delta(content: str) -> None:
                    nonlocal received_delta
                    received_delta = True
                    await self._emit("text_delta", content=content)

                provider_reply: ToolChatResult | Awaitable[ToolChatResult] = tool_chat_completion(
                    messages=messages,
                    tools=tool_schemas,
                    tool_choice=tool_choice,
                    timeout=remaining,
                    on_content_delta=on_delta,
                    cancel_event=self.cancel_event,
                    deadline=self._deadline,
                    stream_response=stream_response,
                )
                reply = await _resolve_tool_reply(provider_reply)
                iteration += 1
                names = [call["name"] for call in reply.tool_calls]
                add_trace_event(self.trace_id, "model_decision", iteration, {"tool_calls": names})
                if not reply.tool_calls:
                    return await self._finish(reply.content, "final_response", success=True, already_streamed=received_delta)
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
                    if tool_calls >= self.config.max_tool_calls:
                        result = {"error": "tool_budget_exceeded"}
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": _tool_context(result)})
                        await self._emit("tool_result", tool_name=call["name"], result=result)
                        continue
                    result, paused = await self._execute(registry, call, iteration)
                    if paused:
                        state = {
                            "messages": messages,
                            "iteration": iteration,
                            "tool_calls": tool_calls,
                            "elapsed": self._elapsed(),
                            # Two terminal pause events are emitted after this state is saved.
                            "sequence": self._sequence + 2,
                            "request_id": result["request_id"],
                            "pending_call": result["pending_call"],
                        }
                        save_run(self.session_id, self.profile_id, self.trace_id, "waiting_approval", state)
                        mark_waiting_approval(self.session_id, result["request_id"], call["name"])
                        complete_trace(self.trace_id, "waiting_approval")
                        await self._emit(
                            "permission_required",
                            request_id=result["request_id"],
                            tool_name=call["name"],
                            risk_level=get_tool_risk(call["name"]).value,
                            params=result["public_params"],
                            message="Tool call requires user approval",
                        )
                        await self._emit("run_complete", status="waiting_approval", success=True)
                        return AgentResult(self.session_id, self.trace_id, True, events=self.events, waiting_approval=True)
                    if call["name"] in _READ_ONLY_TOOL_NAMES and not _tool_result_has_error(result):
                        return await self._finish(
                            _render_read_only_tool_result(call["name"], result),
                            "tool_result_complete",
                            success=True,
                        )
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": _tool_context(result)})
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
        add_trace_event(self.trace_id, "tool_call", iteration, {"tool_name": name})
        risk = get_tool_risk(name)
        if risk.value in {"high", "medium"}:
            public_params = {"key": params.get("key", ""), "category": params.get("category", "general")}
            request_id = create_approval(
                self.session_id,
                self.profile_id,
                name,
                risk.value,
                params,
                flow_kind="coach",
                trace_id=self.trace_id,
                public_params=public_params,
            )
            write_audit_log(self.session_id, name, risk.value, "request", public_params)
            return {
                "request_id": request_id,
                "pending_call": {"id": call["id"], "name": name, "params": params},
                "public_params": public_params,
            }, True
        result = await self._execute_tool(registry, name, params)
        await self._emit("tool_result", tool_name=name, result=result)
        add_trace_event(self.trace_id, "tool_result", iteration, {"tool_name": name})
        return result, False

    async def _execute_tool(self, registry: ToolRegistry, name: str, params: dict[str, Any]) -> Any:
        self._raise_if_cancelled()
        tool = registry.get(name)
        if tool is None:
            raise ValueError("unknown_tool")
        result = await await_with_deadline(
            asyncio.to_thread(tool.execute, params),
            deadline=self._deadline,
            cancel_event=self.cancel_event,
        )
        if inspect.isawaitable(result):
            return await await_with_deadline(
                result, deadline=self._deadline, cancel_event=self.cancel_event
            )
        return result

    def _registry(self) -> ToolRegistry:
        return create_coach_registry(
            self.session_id,
            self.profile_id,
            self.trace_id,
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
            content = content[: max(0, self.config.max_output_chars - self._output_chars)]
        if content:
            add_message(self.session_id, "assistant", content)
            if not already_streamed:
                await self._emit("text_delta", content=content)
        status = "cancelled" if cancelled else ("completed" if success else "failed")
        save_run(self.session_id, self.profile_id, self.trace_id, status, {})
        complete_trace(self.trace_id, status)
        await self._emit("final_response", content=content, termination_reason=reason)
        await self._emit("run_complete", status=status, success=success, error=error)
        return AgentResult(self.session_id, self.trace_id, success, final_output=content, events=self.events, error=error)

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
            "trace_id": self.trace_id,
            "sequence": self._sequence,
            **data,
        }
        self.events.append(event)
        if self._queue is not None and self._consumer_open:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    self._queue.put_nowait(event)
                except asyncio.QueueFull:
                    return

    def _raise_if_cancelled(self) -> None:
        raise_if_cancelled(self.cancel_event)

    def _remaining(self) -> float:
        return remaining_seconds(self._deadline)

    def _elapsed(self) -> float:
        return max(0.0, self.config.total_timeout_seconds - max(0.0, self._remaining()))

    def _truncate_output(self, content: str) -> str:
        remaining = self.config.max_output_chars - self._output_chars
        if remaining <= 0:
            return ""
        bounded = content[:remaining]
        self._output_chars += len(bounded)
        return bounded


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


async def _resolve_tool_reply(value: ToolChatResult | Awaitable[ToolChatResult]) -> ToolChatResult:
    """Keep synchronous test doubles compatible with the asynchronous provider contract."""
    if inspect.isawaitable(value):
        return await value
    return value


def _tool_context(result: Any) -> str:
    """Keep all tool-result messages within the shared model-context budget."""
    if isinstance(result, list) and all(isinstance(item, dict) for item in result):
        knowledge_items = [item for item in result if "question" in item and "source" in item]
        if knowledge_items:
            compact_results = []
            for item in knowledge_items[:3]:
                compact_results.append(
                    {
                        "title": str(item.get("title", ""))[:160],
                        "question": str(item.get("question", ""))[:400],
                        "expert_answer": str(item.get("expert_answer") or item.get("content") or "")[:600],
                        "exam_points": [str(point)[:160] for point in item.get("exam_points", [])[:4]],
                        "source": str(item.get("source", ""))[:240],
                    }
                )
            return json.dumps({"results": compact_results, "truncated": len(knowledge_items) > 3}, ensure_ascii=False)
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    if len(serialized) <= MAX_TOOL_CONTEXT_CHARS:
        return serialized
    return json.dumps(
        {
            "truncated": True,
            "preview": serialized[: MAX_TOOL_CONTEXT_CHARS - 100],
        },
        ensure_ascii=False,
    )


def _tool_result_has_error(result: Any) -> bool:
    return isinstance(result, dict) and bool(result.get("error"))


def _render_read_only_tool_result(tool_name: str, result: Any) -> str:
    """Render a bounded answer after a successful read-only tool call."""
    if tool_name == "search_knowledge":
        items = result if isinstance(result, list) else []
        if not items:
            return "题库中暂未找到匹配内容。请换一种技术关键词后再试。"
        item = items[0]
        title = str(item.get("title") or item.get("question") or "题库练习题")[:240]
        points = [str(point)[:120] for point in item.get("exam_points", [])[:3]]
        summary = f"已从题库检索到练习题：{title}。"
        if points:
            summary += "\n重点考察：" + "；".join(points)
        return summary + "\n请先给出你的回答，我会继续追问。"
    if tool_name == "recommend_next_question":
        item = result if isinstance(result, dict) else {}
        question = str(item.get("question") or "题库练习题")[:400]
        return f"推荐下一题：{question}\n请先作答，我会围绕你的回答继续练习。"
    if tool_name == "get_practice_profile":
        count = len(result) if isinstance(result, list) else 0
        return f"已读取 {count} 条已批准的练习偏好，并会在后续追问中参考。"
    if tool_name == "list_recent_reports":
        count = len(result) if isinstance(result, list) else 0
        return f"已找到 {count} 份近期正式诊断报告。"
    return "工具已完成。"
