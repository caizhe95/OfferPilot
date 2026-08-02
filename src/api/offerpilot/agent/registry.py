"""Profile-scoped, schema-validated tools for the restricted Coach Agent."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from offerpilot.agent.types import ToolDefinition
from offerpilot.diagnosis.diagnosis import get_memories, list_recent_reports, save_memory
from offerpilot.knowledge.knowledge_importer import search_knowledge_safe_async


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchKnowledgeArgs(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(default="", max_length=7000)
    limit: int = Field(default=5, ge=1, le=5)


class EmptyArgs(StrictModel):
    pass


class RecentReportsArgs(StrictModel):
    limit: int = Field(default=5, ge=1, le=10)


class RecommendArgs(StrictModel):
    focus: str = Field(default="", max_length=1000)


class SaveMemoryArgs(StrictModel):
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=300)
    category: str = Field(default="general", max_length=64)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters}} for tool in self._tools.values()]

    def validate(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        tool = self.get(name)
        if tool is None or tool.validator is None:
            raise ValueError("unknown_tool")
        return tool.validator.model_validate(params).model_dump()

    def execute(self, name: str, params: dict[str, Any]) -> Any:
        tool = self.get(name)
        if tool is None:
            raise ValueError("unknown_tool")
        return tool.execute(params)


def _definition(name: str, description: str, validator: type[BaseModel], risk: str, execute: Any) -> ToolDefinition:
    return ToolDefinition(name, description, validator.model_json_schema(), risk, execute, validator)


def create_coach_registry(
    session_id: str,
    profile_id: str,
    trace_id: str,
    deadline: float | None = None,
    cancel_event: asyncio.Event | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_definition(
        "search_knowledge",
        "Search the internal technical interview knowledge base.",
        SearchKnowledgeArgs,
        "low",
        lambda p: search_knowledge_safe_async(
            question=p["question"],
            answer=p["answer"],
            limit=p["limit"],
            trace_id=trace_id,
            deadline=deadline,
            cancel_event=cancel_event,
        ),
    ))
    registry.register(_definition("get_practice_profile", "Read this profile's approved coaching memories only.", EmptyArgs, "low", lambda _p: [{"key": item["key"], "value": item["value"], "category": item["category"]} for item in get_memories(profile_id)[:20]]))
    registry.register(_definition("list_recent_reports", "List this profile's recent diagnosis report summaries.", RecentReportsArgs, "low", lambda p: list_recent_reports(profile_id, p["limit"])))
    registry.register(_definition(
        "recommend_next_question",
        "Recommend a question from the internal knowledge base; never invent one.",
        RecommendArgs,
        "low",
        lambda p: _recommend(p["focus"], trace_id, deadline=deadline, cancel_event=cancel_event),
    ))
    registry.register(_definition(
        "save_memory",
        "Request approval to save one coaching memory.",
        SaveMemoryArgs,
        "high",
        lambda p: save_memory(
            session_id,
            p["key"],
            p["value"],
            p["category"],
            profile_id=profile_id,
            trace_id=trace_id,
        ),
    ))
    return registry


async def _recommend(
    focus: str,
    trace_id: str,
    *,
    deadline: float | None = None,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    items = await search_knowledge_safe_async(
        question=focus or "技术面试练习",
        answer="",
        limit=1,
        trace_id=trace_id,
        deadline=deadline,
        cancel_event=cancel_event,
    )
    if not items:
        return {"question": "", "exam_points": [], "reason": "题库中暂无匹配题目", "source": ""}
    item = items[0]
    return {"question": item.get("question", item.get("title", "")), "exam_points": item.get("exam_points", [])[:5], "reason": "根据内部题库检索结果推荐", "source": item.get("source", item.get("title", ""))}
