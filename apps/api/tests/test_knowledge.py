"""Tests for knowledge parsing, importing, and search."""

import os
from pathlib import Path
import pytest
from app.knowledge.knowledge_parser import parse_markdown, parse_directory
from app.knowledge.knowledge_importer import import_knowledge, search_knowledge, ensure_knowledge_loaded
from app.core.database import get_db, init_db

# Knowledge directory from environment (set in conftest)
KNOWLEDGE_DIR = Path(os.environ.get("KNOWLEDGE_DIR", "knowledge/selected"))


def test_parse_markdown():
    """Should parse a valid Markdown file."""
    path = KNOWLEDGE_DIR / "context-window.md"
    if not path.exists():
        pytest.skip("Knowledge file not found")
    result = parse_markdown(path)
    assert result is not None
    assert "title" in result
    assert "content" in result
    assert "dimension" in result
    assert "source" in result
    assert len(result["content"]) > 100


def test_parse_markdown_nonexistent():
    """Should return None for nonexistent file."""
    result = parse_markdown(Path("/nonexistent/file.md"))
    assert result is None


def test_parse_directory():
    """Should parse all MD files in a directory."""
    if not KNOWLEDGE_DIR.exists():
        pytest.skip("Knowledge directory not found")
    entries = parse_directory(KNOWLEDGE_DIR)
    assert len(entries) >= 1
    for entry in entries:
        assert entry["content"]


def test_import_knowledge():
    """Import should load knowledge into DB."""
    init_db()
    conn = get_db()
    try:
        if not KNOWLEDGE_DIR.exists():
            pytest.skip("Knowledge directory not found")
        count = import_knowledge(conn, KNOWLEDGE_DIR)
        assert count > 0

        # Verify data in knowledge table
        row_count = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        assert row_count == count

        # Verify FTS
        fts_count = conn.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]
        assert fts_count == count
    finally:
        conn.close()


def test_ensure_knowledge_loaded():
    """Should load knowledge if empty, skip if already loaded."""
    init_db()
    conn = get_db()
    try:
        # Clear
        conn.execute("DELETE FROM knowledge_fts")
        conn.execute("DELETE FROM knowledge")
        conn.commit()

        count = ensure_knowledge_loaded(conn)
        assert count >= 1

        # Second call should be idempotent
        count2 = ensure_knowledge_loaded(conn)
        assert count2 == count
    finally:
        conn.close()


def test_search_knowledge_basic():
    """Basic search should return results."""
    init_db()
    conn = get_db()
    try:
        ensure_knowledge_loaded(conn)

        results = search_knowledge(conn, "Context Window")
        assert len(results) > 0
        for r in results:
            assert "title" in r
            assert "content" in r
            assert "score" in r
    finally:
        conn.close()


def test_search_knowledge_react():
    """ReAct query should recall relevant content."""
    init_db()
    conn = get_db()
    try:
        ensure_knowledge_loaded(conn)

        results = search_knowledge(conn, "ReAct loop")
        assert len(results) > 0
        found = any("ReAct" in r["content"] or "react" in r["content"].lower() for r in results)
        assert found
    finally:
        conn.close()


def test_search_knowledge_tool_calling():
    """Tool Calling query should recall relevant content."""
    init_db()
    conn = get_db()
    try:
        ensure_knowledge_loaded(conn)

        results = search_knowledge(conn, "Tool Calling")
        assert len(results) > 0
    finally:
        conn.close()


def test_search_knowledge_harness():
    """Harness query should recall relevant content."""
    init_db()
    conn = get_db()
    try:
        ensure_knowledge_loaded(conn)

        results = search_knowledge(conn, "Harness")
        assert len(results) > 0
    finally:
        conn.close()


def test_search_empty_query_rejected():
    """Empty query should raise ValueError."""
    init_db()
    conn = get_db()
    try:
        with pytest.raises(ValueError, match="non-empty"):
            search_knowledge(conn, "")
    finally:
        conn.close()


def test_search_limit_respected():
    """Limit should be clamped."""
    init_db()
    conn = get_db()
    try:
        ensure_knowledge_loaded(conn)

        results = search_knowledge(conn, "Agent", limit=2)
        assert len(results) <= 2

        results2 = search_knowledge(conn, "Agent", limit=100)
        assert len(results2) <= 20
    finally:
        conn.close()


def test_search_with_dimension_filter():
    """Dimension filter should work."""
    init_db()
    conn = get_db()
    try:
        ensure_knowledge_loaded(conn)

        results = search_knowledge(conn, "Harness", dimension="harness")
        assert len(results) > 0
        for r in results:
            assert r["dimension"] == "harness"
    finally:
        conn.close()
