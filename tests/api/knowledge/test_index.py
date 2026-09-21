"""Tests for source parsing, async index rebuilds, and hybrid retrieval."""

import asyncio
import os
from pathlib import Path

import pytest

from offerpilot.core.config import settings
from offerpilot.database.connection import get_db, init_db
from offerpilot.knowledge import indexer, retrieval
from offerpilot.knowledge.parser import parse_directory, parse_markdown

KNOWLEDGE_DIR = Path(os.environ.get("OFFERPILOT_KNOWLEDGE_DIR", "resources/knowledge"))
ENTRY = {
    "title": "Atomic Embedding", "content": "Vector retrieval uses validated embeddings.", "source": "test/atomic-embedding.md", "dimension": "test", "kind": "interview_qa", "category": "test", "question": "How does vector retrieval work?", "novice_answer": "", "expert_answer": "It compares validated embedding vectors.", "exam_points": ["validated vectors"], "exam_point_ids": ["test-validated-vectors"], "common_gaps": [], "followups": [], "tags": ["embedding", "retrieval"],
}


def test_parser_reads_markdown_and_directory():
    path = KNOWLEDGE_DIR / "09-rag-retrieval" / "hybrid-retrieval.md"
    assert path.exists()
    entry = parse_markdown(path)
    assert entry and entry["content"] and entry["exam_point_ids"]
    assert parse_directory(KNOWLEDGE_DIR)


def test_parser_skips_missing_file():
    assert parse_markdown(Path("C:\\not-a-real-file.md")) is None


@pytest.mark.asyncio
async def test_initial_load_and_fts_retrieval():
    init_db()
    count = await indexer.ensure_knowledge_loaded_async()
    results = await retrieval.search_knowledge(question="RAG 与检索", limit=2)
    assert count >= 1 and results and all(item["content"] for item in results)


@pytest.mark.asyncio
async def test_retrieval_rejects_empty_query_and_bounds_limit():
    init_db()
    with pytest.raises(ValueError, match="non-empty"):
        await retrieval.search_knowledge(question="")
    results = await retrieval.search_knowledge(question="Agent", limit=100)
    assert len(results) <= 20


def test_fts_query_sanitization_is_safe_for_punctuation():
    assert retrieval._sanitize_fts5_query("Explain ReAct tool calling.") == "Explain ReAct tool calling"


@pytest.mark.asyncio
async def test_rebuild_reuses_unchanged_embedding(monkeypatch):
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.invalid")
    calls: list[str] = []
    async def fake_embedding(text, **_): calls.append(text); return [0.1, 0.2, 0.3]
    monkeypatch.setattr(indexer, "parse_directory", lambda _: [ENTRY])
    monkeypatch.setattr(indexer, "embed_text", fake_embedding)
    init_db()
    first = await indexer.import_knowledge_with_stats_async()
    second = await indexer.import_knowledge_with_stats_async()
    assert (first["embedded"], second["reused"], len(calls)) == (1, 1, 1)


@pytest.mark.asyncio
async def test_required_embedding_failure_preserves_active_index(monkeypatch):
    init_db()
    await indexer.ensure_knowledge_loaded_async()
    conn = get_db()
    try: before = [row["source"] for row in conn.execute("SELECT source FROM knowledge ORDER BY id")]
    finally: conn.close()
    monkeypatch.setattr(settings, "require_embedding", True)
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.invalid")
    monkeypatch.setattr(indexer, "parse_directory", lambda _: [ENTRY])
    async def unavailable(*_, **__): raise RuntimeError("provider unavailable")
    monkeypatch.setattr(indexer, "embed_text", unavailable)
    with pytest.raises(indexer.KnowledgeEmbeddingError, match="existing index was preserved"):
        await indexer.import_knowledge_with_stats_async()
    conn = get_db()
    try: assert [row["source"] for row in conn.execute("SELECT source FROM knowledge ORDER BY id")] == before
    finally: conn.close()


@pytest.mark.asyncio
async def test_required_embedding_rejects_non_finite_vector(monkeypatch):
    monkeypatch.setattr(settings, "require_embedding", True)
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.invalid")
    monkeypatch.setattr(indexer, "parse_directory", lambda _: [ENTRY])
    async def non_finite(*_, **__): return [0.1, float("nan")]
    monkeypatch.setattr(indexer, "embed_text", non_finite)
    init_db()
    with pytest.raises(indexer.KnowledgeEmbeddingError):
        await indexer.import_knowledge_with_stats_async()


@pytest.mark.asyncio
async def test_retrieval_keeps_event_loop_responsive(monkeypatch):
    async def loaded(**_): return 1
    def slow_read(*_):
        import time
        time.sleep(0.05)
        return [], []
    monkeypatch.setattr(retrieval, "ensure_knowledge_loaded_async", loaded)
    monkeypatch.setattr(retrieval, "_prepare_search", slow_read)
    task = asyncio.create_task(retrieval.search_knowledge(question="nonblocking"))
    await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.03)
    assert not task.done() and await task == []


@pytest.mark.asyncio
async def test_async_rrf_uses_validated_embedding(monkeypatch):
    monkeypatch.setattr(settings, "require_embedding", True)
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.invalid")
    monkeypatch.setattr(indexer, "parse_directory", lambda _: [ENTRY])
    async def fake_embedding(*_, **__): return [1.0, 0.0, 0.0]
    monkeypatch.setattr(indexer, "embed_text", fake_embedding)
    monkeypatch.setattr(retrieval, "embed_text", fake_embedding)
    init_db()
    await indexer.import_knowledge_with_stats_async()
    results = await retrieval.search_knowledge(question="vector retrieval", limit=1)
    assert results and results[0]["vector_rank"] == 1 and results[0]["rrf_score"] > 0


@pytest.mark.asyncio
async def test_required_retrieval_rejects_embedding_model_mismatch(monkeypatch):
    monkeypatch.setattr(settings, "require_embedding", True)
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_base_url", "https://example.invalid")
    monkeypatch.setattr(settings, "embedding_model", "test-model-a")
    monkeypatch.setattr(indexer, "parse_directory", lambda _: [ENTRY])
    async def fake_embedding(*_, **__): return [1.0, 0.0, 0.0]
    monkeypatch.setattr(indexer, "embed_text", fake_embedding)
    monkeypatch.setattr(retrieval, "embed_text", fake_embedding)
    init_db()
    await indexer.import_knowledge_with_stats_async()
    monkeypatch.setattr(settings, "embedding_model", "test-model-b")
    with pytest.raises(indexer.KnowledgeEmbeddingError, match="configured embedding model"):
        await retrieval.search_knowledge(question="vector retrieval", limit=1)
