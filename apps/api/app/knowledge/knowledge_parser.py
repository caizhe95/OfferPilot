"""Markdown/JSON knowledge parser for interview QA entries."""

import json
import re
from pathlib import Path


def parse_markdown(filepath: Path) -> dict | None:
    """Parse a single Markdown knowledge file.

    Returns a structured interview QA/coaching entry, or None if invalid.
    """
    if not filepath.exists() or filepath.suffix != ".md":
        return None

    content = filepath.read_text(encoding="utf-8").strip()
    if not content:
        return None

    title = filepath.stem.replace("-", " ").replace("_", " ")
    heading_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if heading_match:
        title = heading_match.group(1).strip()

    category = _extract_category(filepath)
    kind = "coaching_doc" if "coaching-methodology" in [p.name for p in filepath.parents] else "interview_qa"
    source = str(filepath.as_posix())
    question = _extract_question(content)
    novice_answer = _extract_inline_or_block(content, "新手答")
    expert_answer = _extract_inline_or_block(content, "高手答")
    exam_points = _extract_bullets(content, "考察点")
    common_gaps = _extract_bullets(content, "常见缺失")
    followups = _extract_bullets(content, "追问")
    tags = _extract_tags(content, category)

    if kind == "interview_qa" and not question:
        # Backward compatibility for old selected docs: use title as a weak question.
        question = title
    if not expert_answer:
        expert_answer = content[:1200]

    return {
        "title": title,
        "content": content,
        "source": source,
        "dimension": category,
        "kind": kind,
        "category": category,
        "question": question,
        "novice_answer": novice_answer,
        "expert_answer": expert_answer,
        "exam_points": exam_points,
        "common_gaps": common_gaps,
        "followups": followups,
        "tags": tags,
    }


def _extract_category(filepath: Path) -> str:
    """Extract category from the nearest knowledge subdirectory."""
    parent = filepath.parent.name.lower()
    if parent and parent != "knowledge":
        return parent

    name = filepath.stem.lower()
    if "architecture" in name:
        return "01-architecture-design"
    if "context" in name:
        return "04-memory-context"
    if "react" in name:
        return "15-agent-concepts"
    if "tool" in name:
        return "02-tool-management"
    if "harness" in name:
        return "07-engineering-pitfalls"
    if "rag" in name:
        return "09-rag-retrieval"
    if "error" in name:
        return "03-fault-tolerance"

    return "general"


def _section(content: str, heading: str) -> str:
    pattern = rf"^##\s*{re.escape(heading)}\s*:?\s*(.*?)$(.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    inline = match.group(1).strip()
    body = match.group(2).strip()
    return "\n".join(part for part in [inline, body] if part).strip()


def _extract_question(content: str) -> str:
    question = _section(content, "Q")
    if question:
        return question.strip("：: \n")
    match = re.search(r"^##\s*Q[：:]\s*(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_inline_or_block(content: str, label: str) -> str:
    pattern = rf"\*\*{re.escape(label)}\*\*[：:]\s*(.*?)(?=^\*\*[^*\n]+\*\*[：:]|^##\s+|\Z)"
    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip().strip('"“”')


def _extract_bullets(content: str, heading: str) -> list[str]:
    section = _section(content, heading)
    if not section:
        return []
    items = []
    for line in section.splitlines():
        cleaned = re.sub(r"^\s*[-*]\s*", "", line).strip()
        if cleaned:
            items.append(cleaned)
    return items


def _extract_tags(content: str, category: str) -> list[str]:
    tags = [category]
    for token in [
        "agent", "rag", "tool calling", "function calling", "memory",
        "permission", "session", "trace", "eval", "embedding", "fts5",
        "fastapi", "sse", "asr", "mimo", "python agent loop",
    ]:
        if token.lower() in content.lower():
            tags.append(token)
    return sorted(set(tags))


def parse_json_file(filepath: Path) -> list[dict]:
    """Parse an external JSON knowledge array file."""
    data = json.loads(filepath.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Knowledge JSON must be an array")
    entries = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", item.get("question", ""))).strip()
        question = str(item.get("question", "")).strip()
        expert_answer = str(item.get("expertAnswer", item.get("expert_answer", item.get("content", "")))).strip()
        novice_answer = str(item.get("noviceAnswer", item.get("novice_answer", ""))).strip()
        if not title or not question:
            continue
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        category = str(item.get("category", item.get("dimension", "general"))).strip() or "general"
        content = str(item.get("content", "")).strip() or f"# {title}\n\n## Q：{question}\n\n**高手答**：\n\n{expert_answer}"
        entries.append({
            "title": title,
            "content": content,
            "source": str(filepath.as_posix()),
            "dimension": category,
            "kind": str(item.get("kind", "interview_qa")),
            "category": category,
            "question": question,
            "novice_answer": novice_answer,
            "expert_answer": expert_answer,
            "exam_points": item.get("exam_points", item.get("examPoints", [])) or [],
            "common_gaps": item.get("common_gaps", item.get("commonGaps", [])) or [],
            "followups": item.get("followups", []) or [],
            "tags": tags,
        })
    return entries


def parse_directory(knowledge_dir: Path) -> list[dict]:
    """Parse all Markdown files in a knowledge directory recursively.

    Returns list of parsed entries.
    """
    entries = []
    if not knowledge_dir.exists():
        return entries

    for md_file in sorted(knowledge_dir.rglob("*.md")):
        if "selected" in md_file.parts:
            continue
        entry = parse_markdown(md_file)
        if entry:
            entries.append(entry)
    for json_file in sorted(knowledge_dir.rglob("*.json")):
        try:
            entries.extend(parse_json_file(json_file))
        except Exception:
            continue

    return entries
