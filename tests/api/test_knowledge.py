"""Tests for knowledge parsing, importing, and search."""

import asyncio
import os
from pathlib import Path
import pytest
from offerpilot.knowledge.knowledge_parser import parse_markdown, parse_directory
from offerpilot.knowledge.knowledge_importer import (
    _sanitize_fts5_query,
    KnowledgeEmbeddingError,
    import_knowledge,
    import_knowledge_with_stats,
    import_knowledge_with_stats_async,
    search_knowledge,
    search_knowledge_safe_async,
    ensure_knowledge_loaded,
)
from offerpilot.core.database import get_db, init_db

# Knowledge directory from environment (set in conftest)
KNOWLEDGE_DIR = Path(os.environ.get("OFFERPILOT_KNOWLEDGE_DIR", "knowledge/selected"))


EMBEDDING_ENTRY = {
    "title": "Atomic Embedding",
    "content": "Vector retrieval uses validated embeddings.",
    "source": "test/atomic-embedding.md",
    "dimension": "test",
    "kind": "interview_qa",
    "category": "test",
    "question": "How does vector retrieval work?",
    "novice_answer": "",
    "expert_answer": "It compares validated embedding vectors.",
    "exam_points": ["validated vectors"],
    "common_gaps": [],
    "followups": [],
    "tags": ["embedding", "retrieval"],
}


def test_parse_markdown():
    """Should parse a valid Markdown file."""
    path = KNOWLEDGE_DIR / "09-rag-retrieval" / "hybrid-retrieval.md"
    if not path.exists():
        pytest.skip("Knowledge file not found")
    result = parse_markdown(path)
    assert result is not None
    assert "title" in result
    assert "content" in result
    assert "kind" in result
    assert "category" in result
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

        results = search_knowledge(conn, "RAG 与检索")
        assert len(results) > 0
        for r in results:
            assert "title" in r
            assert "content" in r
            assert "score" in r
            assert "kind" in r
            assert "category" in r
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


def test_search_with_category_filter():
    """Category filter should work."""
    init_db()
    conn = get_db()
    try:
        ensure_knowledge_loaded(conn)

        results = search_knowledge(conn, "Harness", category="07-engineering-pitfalls")
        assert len(results) > 0
        for r in results:
            assert r["category"] == "07-engineering-pitfalls"
    finally:
        conn.close()


def test_fts5_query_sanitization_removes_sentence_punctuation():
    assert _sanitize_fts5_query("Explain ReAct tool calling.") == "Explain ReAct tool calling"


def test_search_knowledge_accepts_english_sentence_punctuation():
    init_db()
    conn = get_db()
    try:
        results = search_knowledge(conn, "ReAct tool calling.")
        assert isinstance(results, list)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_reload_reuses_embeddings_for_unchanged_content(monkeypatch):
    """Reload should bind a cached vector to the new knowledge row."""
    from offerpilot.core.config import settings
    import offerpilot.knowledge.knowledge_importer as knowledge_importer

    entries = [{
        "title": "Embedding Reload",
        "content": "把文本编码为向量用于语义检索。",
        "source": "test/embedding-reload.md",
        "dimension": "test",
        "kind": "interview_qa",
        "category": "test",
        "question": "什么是 embedding？",
        "novice_answer": "",
        "expert_answer": "把文本编码为向量用于语义检索。",
        "exam_points": ["向量编码"],
        "common_gaps": [],
        "followups": [],
        "tags": ["embedding"],
    }]
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.invalid")
    calls: list[str] = []

    async def fake_embed_text(text: str, **_: object) -> list[float]:
        calls.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(knowledge_importer, "async_embed_text", fake_embed_text)
    monkeypatch.setattr(knowledge_importer, "parse_directory", lambda _: entries)
    init_db()
    conn = get_db()
    try:
        first = await import_knowledge_with_stats_async()
        old_row = conn.execute(
            "SELECT knowledge_id, vector_json FROM knowledge_embeddings"
        ).fetchone()
        second = await import_knowledge_with_stats_async()
        new_row = conn.execute(
            "SELECT knowledge_id, vector_json FROM knowledge_embeddings"
        ).fetchone()
    finally:
        conn.close()

    assert first["embedded"] == 1
    assert first["reused"] == 0
    assert second["embedded"] == 0
    assert second["reused"] == 1
    assert len(calls) == 1
    assert old_row is not None and new_row is not None
    assert new_row["knowledge_id"] != old_row["knowledge_id"]
    assert new_row["vector_json"] == old_row["vector_json"]


def test_required_embedding_rejects_missing_configuration(monkeypatch):
    from offerpilot.core.config import settings

    monkeypatch.setattr(settings, "require_embedding", True)
    monkeypatch.setattr(settings, "embedding_api_key", "")
    monkeypatch.setattr(settings, "embedding_base_url", "")
    init_db()
    conn = get_db()
    try:
        monkeypatch.setattr(settings, "require_embedding", False)
        ensure_knowledge_loaded(conn)
        monkeypatch.setattr(settings, "require_embedding", True)
        with pytest.raises(RuntimeError, match="Embedding service"):
            search_knowledge(conn, "ReAct")
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_required_embedding_failure_preserves_existing_index(monkeypatch):
    from offerpilot.core.config import settings
    import offerpilot.knowledge.knowledge_importer as knowledge_importer

    init_db()
    await import_knowledge_with_stats_async()
    conn = get_db()
    try:
        original_sources = [row["source"] for row in conn.execute("SELECT source FROM knowledge ORDER BY id").fetchall()]
    finally:
        conn.close()

    monkeypatch.setattr(settings, "require_embedding", True)
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.invalid")
    monkeypatch.setattr(knowledge_importer, "parse_directory", lambda _: [EMBEDDING_ENTRY])

    async def unavailable_embedding(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(knowledge_importer, "async_embed_text", unavailable_embedding)
    with pytest.raises(KnowledgeEmbeddingError, match="existing index was preserved"):
        await import_knowledge_with_stats_async()

    conn = get_db()
    try:
        assert [row["source"] for row in conn.execute("SELECT source FROM knowledge ORDER BY id").fetchall()] == original_sources
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_required_embedding_rejects_non_finite_vector(monkeypatch):
    from offerpilot.core.config import settings
    import offerpilot.knowledge.knowledge_importer as knowledge_importer

    monkeypatch.setattr(settings, "require_embedding", True)
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.invalid")
    monkeypatch.setattr(knowledge_importer, "parse_directory", lambda _: [EMBEDDING_ENTRY])

    async def non_finite_embedding(*_args, **_kwargs):
        return [0.1, float("nan")]

    monkeypatch.setattr(knowledge_importer, "async_embed_text", non_finite_embedding)
    init_db()
    with pytest.raises(KnowledgeEmbeddingError, match="existing index was preserved"):
        await import_knowledge_with_stats_async()
    conn = get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_async_search_keeps_event_loop_responsive(monkeypatch):
    import offerpilot.knowledge.knowledge_importer as knowledge_importer

    async def loaded(**_kwargs: object) -> int:
        return 1

    def slow_read(*_args, **_kwargs):
        import time

        time.sleep(0.05)
        return [], []

    monkeypatch.setattr(knowledge_importer, "ensure_knowledge_loaded_async", loaded)
    monkeypatch.setattr(knowledge_importer, "_prepare_async_search", slow_read)
    task = asyncio.create_task(search_knowledge_safe_async(question="nonblocking"))
    await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.03)
    assert not task.done()
    assert await task == []


@pytest.mark.asyncio
async def test_async_rrf_search_uses_validated_mock_embedding(monkeypatch):
    from offerpilot.core.config import settings
    import offerpilot.knowledge.knowledge_importer as knowledge_importer

    monkeypatch.setattr(settings, "require_embedding", True)
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.invalid")
    monkeypatch.setattr(knowledge_importer, "parse_directory", lambda _: [EMBEDDING_ENTRY])

    async def fake_embedding(*_args, **_kwargs):
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(knowledge_importer, "async_embed_text", fake_embedding)
    init_db()
    await import_knowledge_with_stats_async()
    results = await search_knowledge_safe_async(question="vector retrieval", limit=1)
    assert len(results) == 1
    assert results[0]["vector_rank"] == 1
    assert results[0]["rrf_score"] > 0


@pytest.mark.asyncio
async def test_required_async_search_rejects_embedding_model_mismatch(monkeypatch):
    from offerpilot.core.config import settings
    import offerpilot.knowledge.knowledge_importer as knowledge_importer

    monkeypatch.setattr(settings, "require_embedding", True)
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "embedding_model", "test-model-a")
    monkeypatch.setattr(knowledge_importer, "parse_directory", lambda _: [EMBEDDING_ENTRY])

    async def fake_embedding(*_args, **_kwargs):
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(knowledge_importer, "async_embed_text", fake_embedding)
    init_db()
    await import_knowledge_with_stats_async()
    monkeypatch.setattr(settings, "embedding_model", "test-model-b")
    with pytest.raises(KnowledgeEmbeddingError, match="configured embedding model"):
        await search_knowledge_safe_async(question="vector retrieval", limit=1)
