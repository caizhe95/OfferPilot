"""Context builder for the fixed single-question diagnosis workflow."""

from offerpilot.diagnosis.diagnosis import get_memories
from offerpilot.harness.harness import load_diagnosis_rules
from offerpilot.session.session import get_recent_messages


def build_context(
    session_id: str,
    profile_id: str,
    user_input: str,
    knowledge_results: list[dict] | None = None,
    max_chars: int = 8000,
    recent_n: int = 10,
) -> str:
    """Assemble the instruction actually sent to the one diagnosis call."""
    layers: list[tuple[str, str]] = [
        ("system", _build_system_prompt()),
        ("rules", load_diagnosis_rules()),
    ]

    memory = _build_memory_context(profile_id)
    if memory:
        layers.append(("memory", memory))

    recent = get_recent_messages(session_id, n=recent_n)
    if recent:
        layers.append(("history", "\n".join(f"[{m['role']}]: {m['content']}" for m in recent)))

    if knowledge_results:
        layers.append(("knowledge", _format_knowledge(knowledge_results)))
    layers.append(("input", user_input))

    parts: list[str] = []
    total_chars = 0
    for name, content in layers:
        if not content:
            continue
        header = f"\n<!-- {name.upper()} -->\n"
        remaining = max_chars - total_chars - len(header)
        if remaining <= 100:
            break
        if len(content) > remaining:
            content = content[:remaining] + "\n...(truncated)"
        parts.append(header + content)
        total_chars += len(header) + len(content)

    assembled = "\n".join(parts)
    input_marker = f"\n<!-- INPUT -->\n{user_input}"
    if user_input not in assembled:
        assembled = assembled[: max(0, max_chars - len(input_marker))] + input_marker
    return assembled


def _build_system_prompt() -> str:
    return """You are an expert Chinese AI Agent / LLM engineering interview evaluator.

你不是知识库问答助手。
你的任务仅限于处理“面试题 + 候选人回答”的诊断，不处理开放域知识问答。
Reference Answers 只用于对标候选人回答，不用于回答知识问题。

Guidelines:
- Be specific and actionable in your feedback.
- Follow the supplied diagnosis rules and structured output contract.
- Do not invent sources or claim knowledge not in the provided knowledge base.
- Always output in Chinese (Simplified).
- Keep responses under 2500 characters."""


def _build_memory_context(profile_id: str) -> str:
    """Build a bounded summary from approved memories of one profile."""
    weaknesses = get_memories(profile_id=profile_id, key="weakness")
    strengths = get_memories(profile_id=profile_id, key="strength")
    target_roles = get_memories(profile_id=profile_id, key="target_role")
    preferences = get_memories(profile_id=profile_id, key="preference")
    if not weaknesses and not strengths and not target_roles and not preferences:
        return ""

    parts = ["## Approved Practice Memory"]
    for item in weaknesses[:5]:
        parts.append(f"- [weakness] {item['value']}")
    for item in strengths[:3]:
        parts.append(f"- [strength] {item['value']}")
    for item in target_roles[:3]:
        parts.append(f"- [target role] {item['value']}")
    for item in preferences[:3]:
        parts.append(f"- [preference] {item['value']}")
    return "\n".join(parts)[:800]


def _format_knowledge(results: list[dict]) -> str:
    """Format fused interview references as evidence, not QA material."""
    refs = [r for r in results[:5] if r.get("kind") == "interview_qa"]
    coaching = [r for r in results[:5] if r.get("kind") == "coaching_doc"]
    parts = ["## Reference Answers"]
    for index, item in enumerate(refs, 1):
        parts.append(f"### {index}. {item.get('title', '')}")
        parts.append(f"Question: {item.get('question', '')}")
        expert = str(item.get("expert_answer") or item.get("content") or "")
        parts.append(f"Expert Answer:\n{expert[:900]}")
        points = item.get("exam_points") or []
        if points:
            parts.append("Exam Points:\n" + "\n".join(f"- {point}" for point in points[:6]))
        parts.append(f"Source: {item.get('source', '')}")
    for index, item in enumerate(coaching[:2], 1):
        parts.append(f"### Coaching Note {index}: {item.get('title', '')}\n{str(item.get('content', ''))[:700]}")
    return "\n\n".join(parts)
