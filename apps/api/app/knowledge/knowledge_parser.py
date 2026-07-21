"""Markdown knowledge parser.

Parses Markdown files from the knowledge directory into structured
entries for storage in SQLite + FTS5.
"""

import re
from pathlib import Path


def parse_markdown(filepath: Path) -> dict | None:
    """Parse a single Markdown knowledge file.

    Returns a dict with title, content, source, dimension, or None if invalid.
    """
    if not filepath.exists() or filepath.suffix != ".md":
        return None

    content = filepath.read_text(encoding="utf-8").strip()
    if not content:
        return None

    # Extract title from first # heading
    title = filepath.stem.replace("-", " ").replace("_", " ")
    heading_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if heading_match:
        title = heading_match.group(1).strip()

    # Determine dimension from path
    dimension = _extract_dimension(filepath)

    # Source is the filename without extension
    source = filepath.stem

    return {
        "title": title,
        "content": content,
        "source": source,
        "dimension": dimension,
    }


def _extract_dimension(filepath: Path) -> str:
    """Extract dimension tag from file path or content.

    Looks for parent directory naming patterns and content keywords.
    """
    # Check parent directory name
    parent = filepath.parent.name.lower()
    if "architecture" in parent:
        return "architecture"
    if "engineering" in parent:
        return "engineering"
    if "tool" in parent:
        return "tool_calling"
    if "fault" in parent:
        return "fault_tolerance"
    if "memory" in parent:
        return "memory_context"
    if "rag" in parent:
        return "rag"
    if "eval" in parent:
        return "evaluation"
    if "multi-agent" in parent:
        return "multi_agent"
    if "prompt" in parent:
        return "prompt_engineering"
    if "model" in parent:
        return "model"
    if "streaming" in parent or "full-stack" in parent:
        return "streaming"

    # Fallback: keyword match in filename
    name = filepath.stem.lower()
    if "context" in name:
        return "context_window"
    if "react" in name:
        return "react_loop"
    if "tool" in name:
        return "tool_calling"
    if "harness" in name:
        return "harness"
    if "error" in name:
        return "error_handling"

    return "general"


def parse_directory(knowledge_dir: Path) -> list[dict]:
    """Parse all Markdown files in a knowledge directory recursively.

    Returns list of parsed entries.
    """
    entries = []
    if not knowledge_dir.exists():
        return entries

    for md_file in sorted(knowledge_dir.rglob("*.md")):
        entry = parse_markdown(md_file)
        if entry:
            entries.append(entry)

    return entries
