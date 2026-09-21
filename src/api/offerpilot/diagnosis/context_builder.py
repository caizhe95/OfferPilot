"""Priority-aware context construction."""

from __future__ import annotations

from offerpilot.diagnosis.reporting import load_diagnosis_rules
from offerpilot.profiles.memory_repository import get_memories
from offerpilot.sessions.repository import get_messages
from offerpilot.sessions.summaries import get_session_summary


def build_context(session_id: str, profile_id: str, user_input: str, knowledge_results: list[dict] | None = None, max_chars: int = 24000, recent_n: int = 12) -> str:
    layers: list[tuple[str, str, bool]] = [
        ("system", _build_system_prompt(), True),
        ("rules", load_diagnosis_rules(), True),
        ("input", user_input, True),
        ("knowledge", _format_knowledge(knowledge_results or []), False),
        ("summary", _summary_context(session_id, profile_id), False),
        ("memory", _memory_context(profile_id), False),
        ("history", _history_context(session_id, profile_id, recent_n), False),
    ]
    def header(name: str) -> str:
        return f"\n<!-- {name.upper()} -->\n"

    truncated_suffix = "\n...(truncated)"
    reserved = sum(len(header(name)) + len(content) for name, content, required in layers if required and content)
    if reserved > max_chars:
        raise ValueError("required_context_over_budget")
    remaining = max_chars - reserved
    parts: list[str] = []
    for name, content, required in layers:
        if not content:
            continue
        if required:
            bounded = content
            suffix = ""
        else:
            section_header = header(name)
            full_length = len(section_header) + len(content)
            if full_length <= remaining:
                bounded = content
                suffix = ""
            elif remaining > len(section_header) + len(truncated_suffix):
                content_length = remaining - len(section_header) - len(truncated_suffix)
                bounded = content[:content_length]
                suffix = truncated_suffix
            else:
                continue
            remaining -= len(section_header) + len(bounded) + len(suffix)
        parts.append(header(name) + bounded + suffix)
    assembled = "".join(parts)
    if user_input not in assembled:
        raise ValueError("required_context_over_budget")
    return assembled


def _build_system_prompt() -> str:
    return """You are an expert Chinese AI Agent / LLM engineering interview evaluator.
你只处理“面试题 + 候选人回答”的正式诊断，不处理开放域知识问答。
历史 Summary、批准 Memory 和最近消息只能作为练习背景，不能作为当前回答的评分证据。
正式评分只能依据当前回答、当前题目、程序化表达特征和检索到的参考考点。
Always output in Simplified Chinese and follow the structured output contract."""


def _history_context(session_id: str, profile_id: str, recent_n: int) -> str:
    messages = get_messages(session_id, n=recent_n, profile_id=profile_id)
    return "\n".join(f"[{item['role']}]: {item['content']}" for item in messages)


def _memory_context(profile_id: str) -> str:
    memories = get_memories(profile_id)
    if not memories:
        return ""
    return "## Approved Practice Memory\n" + "\n".join(f"- [{item['key']}] {item['value']}" for item in memories[:12])


def _summary_context(session_id: str, profile_id: str) -> str:
    summary = get_session_summary(session_id, profile_id)
    if not summary:
        return ""
    return "## Session Historical Summary (not scoring evidence)\n" + str(summary.get("summary_json", summary))


def _format_knowledge(results: list[dict]) -> str:
    parts = ["## Reference Answers"]
    for index, item in enumerate([item for item in results[:5] if item.get("kind") == "interview_qa"], 1):
        parts.append(f"### {index}. {item.get('title', '')}")
        parts.append(f"Question: {item.get('question', '')}")
        parts.append(f"Expert Answer:\n{str(item.get('expert_answer') or item.get('content') or '')[:1200]}")
        points = item.get("exam_point_refs") or []
        if points:
            parts.append("Exam Points:\n" + "\n".join(f"- [{point.get('id', '')}] {point.get('label', '')}" for point in points[:8]))
        parts.append(f"Source: {item.get('source', '')}")
    return "\n\n".join(parts) if len(parts) > 1 else ""
