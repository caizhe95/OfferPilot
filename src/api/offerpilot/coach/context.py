"""Context selection and safe rendering for Coach model turns."""

from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """You are OfferPilot Lite's restricted technical interview coach. You may only practice, review, ask interview follow-ups, and recommend questions using the listed tools. Formal scoring is available only through the explicit diagnosis mode, never through this conversation. You are not an open-domain assistant: reject resumes, job descriptions, and general job-search requests. When the user asks for knowledge lookup, question recommendation, historical reports, or approved memory, you MUST call the matching listed tool before answering; never claim a retrieved result without its tool result. Otherwise answer in concise Simplified Chinese."""
HISTORY_MESSAGES = 12
HISTORY_CHARS = 12000
MAX_TOOL_CONTEXT_CHARS = 12000

READ_ONLY_TOOL_NAMES = {
    "search_knowledge",
    "recommend_next_question",
    "get_practice_profile",
    "list_recent_reports",
}


def classify_coach_intent(text: str) -> str:
    """Classify the small set of intents that controls Coach tool exposure."""
    normalized = (text or "").strip().lower()
    if any(token in normalized for token in ("save memory", "remember this", "保存记忆", "记住这个")):
        return "save_memory"
    if any(token in normalized for token in ("recent report", "history report", "历史报告", "最近报告")):
        return "recent_reports"
    if any(token in normalized for token in ("my memory", "practice profile", "我的记忆", "练习偏好")):
        return "practice_profile"
    if any(token in normalized for token in ("recommend", "推荐")):
        return "recommend_question"
    if any(token in normalized for token in ("search", "knowledge", "lookup", "检索", "题库", "知识库")):
        return "knowledge_search"
    if any(token in normalized for token in ("score", "rate", "evaluate", "评分", "几分", "评价", "评估", "诊断")):
        return "diagnosis_request"
    return "general"


def tool_names_for_turn(messages: list[dict[str, Any]]) -> set[str]:
    """Expose only the tool surface relevant to the latest user turn."""
    if messages and messages[-1].get("role") == "tool":
        return set()
    latest_user_text = next(
        (str(message.get("content", "")).lower() for message in reversed(messages) if message.get("role") == "user"),
        "",
    )
    intent = classify_coach_intent(latest_user_text)
    if intent == "save_memory":
        return {"save_memory"}
    if intent == "recent_reports":
        return {"list_recent_reports"}
    if intent == "practice_profile":
        return {"get_practice_profile"}
    if intent == "recommend_question":
        return {"recommend_next_question"}
    if intent == "knowledge_search":
        return {"search_knowledge"}
    return set()


def bounded_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Keep history in budget without truncating the newest complete input."""
    selected: list[dict[str, str]] = []
    used = 0
    for message in reversed(messages[-HISTORY_MESSAGES:]):
        content = str(message.get("content", ""))
        if used + len(content) > HISTORY_CHARS:
            break
        selected.append({"role": message["role"], "content": content})
        used += len(content)
    return list(reversed(selected))


def tool_context(result: Any) -> str:
    """Serialize a bounded, safe tool result for the next model turn."""
    if isinstance(result, list) and all(isinstance(item, dict) for item in result):
        knowledge_items = [item for item in result if "question" in item and "source" in item]
        if knowledge_items:
            compact_results = [
                {
                    "title": str(item.get("title", ""))[:160],
                    "question": str(item.get("question", ""))[:400],
                    "expert_answer": str(item.get("expert_answer") or item.get("content") or "")[:600],
                    "exam_points": [str(point)[:160] for point in item.get("exam_points", [])[:4]],
                    "source": str(item.get("source", ""))[:240],
                }
                for item in knowledge_items[:3]
            ]
            return json.dumps({"results": compact_results, "truncated": len(knowledge_items) > 3}, ensure_ascii=False)
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    if len(serialized) <= MAX_TOOL_CONTEXT_CHARS:
        return serialized
    return json.dumps(
        {"truncated": True, "preview": serialized[: MAX_TOOL_CONTEXT_CHARS - 100]},
        ensure_ascii=False,
    )


def tool_result_has_error(result: Any) -> bool:
    return isinstance(result, dict) and bool(result.get("error"))


def render_read_only_tool_result(tool_name: str, result: Any) -> str:
    """Render a concise Coach message after a successful read-only tool call."""
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
