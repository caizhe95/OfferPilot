"""Profile-scoped, schema-validated tools for the restricted Coach Agent."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from offerpilot.coach.models import ToolDefinition
from offerpilot.diagnosis.repository import list_recent_reports
from offerpilot.knowledge.retrieval import search_knowledge
from offerpilot.profiles.memory_repository import get_memories, save_memory
from offerpilot.runs.events import notify
from offerpilot.runs.repository import append_event


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
    def __init__(self, definitions: list[ToolDefinition]) -> None:
        self._tools = {definition.name: definition for definition in definitions}

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


def _definition(name: str, description: str, validator: type[BaseModel], execute: Any) -> ToolDefinition:
    return ToolDefinition(name, description, validator.model_json_schema(), execute, validator)


def create_coach_registry(
    session_id: str,
    profile_id: str,
    run_id: str,
    deadline: float | None = None,
    cancel_event: asyncio.Event | None = None,
) -> ToolRegistry:
    return ToolRegistry([
        _definition(
        "search_knowledge",
        "Search the internal technical interview knowledge base.",
        SearchKnowledgeArgs,
        lambda p: _search_knowledge_for_run(
            run_id,
            session_id,
            profile_id,
            question=p["question"],
            answer=p["answer"],
            limit=p["limit"],
            deadline=deadline,
            cancel_event=cancel_event,
        ),
        ),
        _definition("get_practice_profile", "Read this profile's approved coaching memories only.", EmptyArgs, lambda _p: [{"key": item["key"], "value": item["value"], "category": item["category"]} for item in get_memories(profile_id)[:20]]),
        _definition("list_recent_reports", "List this profile's recent diagnosis report summaries.", RecentReportsArgs, lambda p: list_recent_reports(profile_id, p["limit"])),
        _definition(
        "recommend_next_question",
        "Recommend a question from the internal knowledge base; never invent one.",
        RecommendArgs,
        lambda p: _recommend(p["focus"], run_id, session_id, profile_id, deadline=deadline, cancel_event=cancel_event),
        ),
        _definition(
        "save_memory",
        "Request approval to save one coaching memory.",
        SaveMemoryArgs,
        lambda p: save_memory(
            session_id=session_id,
            profile_id=profile_id,
            key=p["key"],
            value=p["value"],
            category=p["category"],
            run_id=run_id,
        ),
        ),
    ])


async def _recommend(
    focus: str,
    run_id: str,
    session_id: str,
    profile_id: str,
    *,
    deadline: float | None = None,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    items = await _search_knowledge_for_run(
        run_id,
        session_id,
        profile_id,
        question=focus or "技术面试练习",
        answer="",
        limit=1,
        deadline=deadline,
        cancel_event=cancel_event,
    )
    if not items:
        return {"question": "", "exam_points": [], "reason": "题库中暂无匹配题目", "source": ""}
    item = items[0]
    return {"question": item.get("question", item.get("title", "")), "exam_points": item.get("exam_points", [])[:5], "reason": "根据内部题库检索结果推荐", "source": item.get("source", item.get("title", ""))}


async def _search_knowledge_for_run(
    run_id: str,
    session_id: str,
    profile_id: str,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    from time import perf_counter

    started = perf_counter()
    try:
        results = await search_knowledge(
            **kwargs,
            run_id=run_id,
            session_id=session_id,
            profile_id=profile_id,
        )
    except Exception as exc:
        append_event(run_id, "knowledge_error", {"error": exc.__class__.__name__.lower()})
        notify(run_id)
        raise
    append_event(run_id, "knowledge_merged", {
        "fts_count": sum(1 for item in results if item.get("fts_rank")),
        "vector_count": sum(1 for item in results if item.get("vector_rank")),
        "count": len(results),
        "embedding_unavailable": any(item.get("embedding_unavailable") for item in results),
        "duration_ms": max(0, int(round((perf_counter() - started) * 1000))),
    })
    notify(run_id)
    return results
