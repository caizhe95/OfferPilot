"""Context builder: assembles multi-layer context for the Agent.

Context layers (in order of priority):
1. System Prompt
2. Global Rules
3. Triggered Skill + References
4. Recent Messages (N messages window)
5. Memory Summary
6. Retrieved Knowledge
7. Current Input
"""

from app.harness.harness import load_rules_for_skill
from app.skills.skills_loader import load_skill, load_skill_references
from app.session.session import get_recent_messages
from app.diagnosis.diagnosis import get_memories


def build_context(
    session_id: str,
    user_input: str,
    skill_name: str,
    knowledge_results: list[dict] | None = None,
    max_chars: int = 8000,
    recent_n: int = 10,
) -> str:
    """Build a multi-layer context string for the Agent.

    Args:
        session_id: Current session ID
        user_input: Current user message
        skill_name: Matched skill name
        knowledge_results: FTS5 search results
        max_chars: Maximum context length in characters
        recent_n: Number of recent messages to include

    Returns:
        Assembled context string with layers separated by markers.
    """
    layers = []

    # Layer 1: System Prompt
    system_prompt = _build_system_prompt(skill_name)
    layers.append(("system", system_prompt))

    # Layer 2: Global + Skill Rules
    rules = load_rules_for_skill(skill_name)
    if rules:
        layers.append(("rules", rules))

    # Layer 3: Skill body + references
    skill_body = _build_skill_context(skill_name)
    if skill_body:
        layers.append(("skill", skill_body))

    # Layer 4: Recent Messages
    recent = get_recent_messages(session_id, n=recent_n)
    if recent:
        msgs_text = "\n".join(f"[{m['role']}]: {m['content']}" for m in recent)
        layers.append(("history", msgs_text))

    # Layer 5: Memory Summary
    memory = _build_memory_context(session_id)
    if memory:
        layers.append(("memory", memory))

    # Layer 6: Retrieved Knowledge
    if knowledge_results:
        knowledge_text = _format_knowledge(knowledge_results)
        layers.append(("knowledge", knowledge_text))

    # Layer 7: Current Input
    layers.append(("input", user_input))

    # Assemble with layer markers
    context_parts = []
    total_chars = 0
    for layer_name, layer_content in layers:
        header = f"\n<!-- {layer_name.upper()} -->\n"
        content = layer_content
        if total_chars + len(header) + len(content) > max_chars:
            remaining = max_chars - total_chars - len(header)
            if remaining > 100:
                content = content[:remaining] + "\n...(truncated)"
            else:
                break
        context_parts.append(header + content)
        total_chars += len(header) + len(content)

    return "\n".join(context_parts)


def _build_system_prompt(skill_name: str) -> str:
    """Build the system prompt for a given skill."""
    skill = load_skill(skill_name)
    skill_desc = skill["description"] if skill else "AI Agent / LLM engineering interview diagnosis"

    return f"""You are an expert in {skill_desc}.

Your task is to help diagnose and improve interview answers for AI Agent / LLM engineering positions.

Guidelines:
- Be specific and actionable in your feedback.
- Follow the output contract specified in the skill references.
- Do not invent sources or claim knowledge not in the provided knowledge base.
- Always output in Chinese (Simplified).
- Keep responses under 2500 characters."""


def _build_skill_context(skill_name: str) -> str:
    """Build skill body + references context."""
    skill = load_skill(skill_name)
    if not skill:
        return ""

    parts = [f"# Skill: {skill['name']}\n{skill['body']}"]

    refs = load_skill_references(skill_name)
    for ref in refs:
        parts.append(f"\n## Reference: {ref['name']}\n{ref['content']}")

    return "\n".join(parts)


def _build_memory_context(session_id: str) -> str:
    """Build memory summary from stored memories, scoped to session."""
    memories = get_memories(session_id=session_id, key="weakness")
    if not memories:
        return ""

    parts = ["## Previous Session Insights"]
    for m in memories[:5]:  # Limit to 5
        parts.append(f"- [{m['category']}] {m['key']}: {m['value']}")

    # Also get strengths
    strengths = get_memories(session_id=session_id, key="strength")
    if strengths:
        parts.append("\n## Strengths")
        for s in strengths[:3]:
            parts.append(f"- {s['value']}")

    summary = "\n".join(parts)
    if len(summary) > 800:
        summary = summary[:800] + "\n...(memory truncated)"
    return summary


def _format_knowledge(results: list[dict]) -> str:
    """Format knowledge search results for context."""
    parts = ["## Retrieved Knowledge"]
    for i, r in enumerate(results[:5]):
        parts.append(f"### [{i + 1}] {r['title']} (dimension: {r['dimension']}, score: {r['score']})")
        # Truncate long content
        content = r["content"]
        if len(content) > 800:
            content = content[:800] + "..."
        parts.append(content)
    return "\n\n".join(parts)

