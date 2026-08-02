"""Knowledge importer and FTS5/embedding retrieval."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from offerpilot.core.config import settings
from offerpilot.core.deadline import RunDeadlineExceeded, await_with_deadline, raise_if_cancelled, require_remaining
from offerpilot.knowledge.knowledge_parser import parse_directory
from offerpilot.llm.llm_client import embed_text as async_embed_text


class KnowledgeEmbeddingError(RuntimeError):
    """The configured embedding contract cannot be satisfied."""


@dataclass(frozen=True)
class _PreparedKnowledgeEntry:
    entry: dict[str, Any]
    content_hash: str
    vector_json: str | None


def _knowledge_dir(knowledge_dir: Path | None = None) -> Path:
    if knowledge_dir is not None:
        return knowledge_dir
    return settings.resolved_knowledge_dir


def get_index_status(conn: sqlite3.Connection) -> dict[str, Any]:
    knowledge_count = int(conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0])
    embedding_count = int(conn.execute("SELECT COUNT(*) FROM knowledge_embeddings WHERE embedding_model = ?", (settings.embedding_model,)).fetchone()[0])
    return {"knowledge_count": knowledge_count, "embedding_count": embedding_count, "embedding_model": settings.embedding_model, "needs_reindex": knowledge_count != embedding_count}


def _assert_sync_compatibility() -> None:
    """Prevent compatibility wrappers from nesting an event loop at runtime."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("Synchronous knowledge APIs cannot run inside the application event loop")


def import_knowledge(conn: sqlite3.Connection, knowledge_dir: Path | None = None) -> int:
    """Compatibility entry point for CLI and synchronous tests only."""
    _ = conn
    _assert_sync_compatibility()
    result = asyncio.run(import_knowledge_with_stats_async(knowledge_dir))
    return int(result["imported"])


def import_knowledge_with_stats(conn: sqlite3.Connection, knowledge_dir: Path | None = None) -> dict[str, Any]:
    """Compatibility entry point for CLI and synchronous tests only."""
    _ = conn
    _assert_sync_compatibility()
    return asyncio.run(import_knowledge_with_stats_async(knowledge_dir))


async def import_knowledge_with_stats_async(
    knowledge_dir: Path | None = None,
    *,
    deadline: float | None = None,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Prepare every entry first, then atomically replace the active index."""
    entries = await await_with_deadline(
        asyncio.to_thread(parse_directory, _knowledge_dir(knowledge_dir)),
        deadline=deadline,
        cancel_event=cancel_event,
    )
    if not entries:
        return {
            "status": "ok",
            "imported": 0,
            "embedded": 0,
            "reused": 0,
            "embedding_unavailable": _embedding_unavailable(),
            "errors": [],
        }

    embedding_unavailable = _embedding_unavailable()
    if settings.require_embedding and embedding_unavailable:
        raise KnowledgeEmbeddingError("Embedding service is required but not configured")

    embedding_cache = await await_with_deadline(
        asyncio.to_thread(_load_embedding_cache_from_database),
        deadline=deadline,
        cancel_event=cancel_event,
    )
    expected_dimension: int | None = None
    prepared: list[_PreparedKnowledgeEntry] = []
    embedded = 0
    reused = 0
    errors: list[dict[str, str]] = []

    for entry in entries:
        raise_if_cancelled(cancel_event)
        require_remaining(deadline)
        text = _embedding_text(entry)
        content_hash = _content_hash(text)
        vector_json: str | None = None
        if not embedding_unavailable:
            cached = embedding_cache.get((settings.embedding_model, content_hash))
            try:
                if cached is not None:
                    vector = _validate_embedding_vector(json.loads(cached), expected_dimension=expected_dimension)
                    vector_json = json.dumps(vector, ensure_ascii=True, separators=(",", ":"))
                    reused += 1
                else:
                    vector = _validate_embedding_vector(
                        await async_embed_text(
                            text,
                            timeout=min(settings.embedding_timeout_seconds, require_remaining(deadline)),
                            cancel_event=cancel_event,
                            deadline=deadline,
                        ),
                        expected_dimension=expected_dimension,
                    )
                    vector_json = json.dumps(vector, ensure_ascii=True, separators=(",", ":"))
                    embedding_cache[(settings.embedding_model, content_hash)] = vector_json
                    embedded += 1
                expected_dimension = len(vector)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if settings.require_embedding:
                    raise KnowledgeEmbeddingError(
                        "Required embedding generation or validation failed; existing index was preserved"
                    ) from exc
                embedding_unavailable = True
                vector_json = None
                prepared = [
                    _PreparedKnowledgeEntry(item.entry, item.content_hash, None)
                    for item in prepared
                ]
                embedded = 0
                reused = 0
                errors.append({"source": str(entry.get("source", "")), "error": "embedding_unavailable"})

        prepared.append(_PreparedKnowledgeEntry(entry, content_hash, vector_json))

    if settings.require_embedding and any(item.vector_json is None for item in prepared):
        raise KnowledgeEmbeddingError("Required embedding is missing; existing index was preserved")

    await await_with_deadline(
        asyncio.to_thread(_replace_knowledge_index, prepared),
        deadline=deadline,
        cancel_event=cancel_event,
    )
    return {
        "status": "ok",
        "imported": len(prepared),
        "embedded": embedded,
        "reused": reused,
        "embedding_unavailable": embedding_unavailable,
        "errors": errors,
    }


def _insert_knowledge(conn: sqlite3.Connection, entry: dict) -> int:
    cursor = conn.execute(
        """
        INSERT INTO knowledge (
            title, content, source, dimension, kind, category, question,
            novice_answer, expert_answer, exam_points, common_gaps, followups, tags
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.get("title", ""),
            entry.get("content", ""),
            entry.get("source", ""),
            entry.get("category", entry.get("dimension", "")),
            entry.get("kind", "interview_qa"),
            entry.get("category", entry.get("dimension", "")),
            entry.get("question", ""),
            entry.get("novice_answer", ""),
            entry.get("expert_answer", ""),
            json.dumps(entry.get("exam_points", []), ensure_ascii=False),
            json.dumps(entry.get("common_gaps", []), ensure_ascii=False),
            json.dumps(entry.get("followups", []), ensure_ascii=False),
            json.dumps(entry.get("tags", []), ensure_ascii=False),
        ),
    )
    row_id = cursor.lastrowid
    if row_id is None:
        raise RuntimeError("knowledge insert did not return a row id")
    return int(row_id)


def _insert_fts(conn: sqlite3.Connection, row_id: int, entry: dict) -> None:
    conn.execute(
        """
        INSERT INTO knowledge_fts (
            rowid, title, question, expert_answer, novice_answer,
            exam_points, common_gaps, tags, category, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            entry.get("title", ""),
            entry.get("question", ""),
            entry.get("expert_answer", ""),
            entry.get("novice_answer", ""),
            "\n".join(entry.get("exam_points", [])),
            "\n".join(entry.get("common_gaps", [])),
            " ".join(entry.get("tags", [])),
            entry.get("category", entry.get("dimension", "")),
            entry.get("source", ""),
        ),
    )


def _load_embedding_cache(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """Return vectors from the current corpus before reload removes their rows."""
    rows = conn.execute(
        "SELECT embedding_model, content_hash, vector_json FROM knowledge_embeddings"
    ).fetchall()
    return {
        (str(row["embedding_model"]), str(row["content_hash"])): str(row["vector_json"])
        for row in rows
    }


def _load_embedding_cache_from_database() -> dict[tuple[str, str], str]:
    from offerpilot.core.database import get_db

    conn = get_db()
    try:
        return _load_embedding_cache(conn)
    finally:
        conn.close()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _insert_embedding(
    conn: sqlite3.Connection,
    knowledge_id: int,
    vector_json: str,
    content_hash: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO knowledge_embeddings
            (knowledge_id, embedding_model, vector_json, content_hash, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            knowledge_id,
            settings.embedding_model,
            vector_json,
            content_hash,
            now,
        ),
    )


def _replace_knowledge_index(prepared: list[_PreparedKnowledgeEntry]) -> None:
    """Replace FTS and vectors only after all required embeddings are ready."""
    from offerpilot.core.database import get_db

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM knowledge_fts")
        conn.execute("DELETE FROM knowledge_embeddings")
        conn.execute("DELETE FROM knowledge")
        for item in prepared:
            row_id = _insert_knowledge(conn, item.entry)
            _insert_fts(conn, row_id, item.entry)
            if item.vector_json is not None:
                _insert_embedding(conn, row_id, item.vector_json, item.content_hash)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _embedding_text(entry: dict) -> str:
    return "\n".join(
        [
            str(entry.get("title", "")),
            str(entry.get("question", "")),
            str(entry.get("expert_answer", "")),
            "\n".join(entry.get("exam_points", [])),
            "\n".join(entry.get("common_gaps", [])),
            " ".join(entry.get("tags", [])),
        ]
    ).strip()


def _embedding_unavailable() -> bool:
    return not settings.embedding_api_key.strip() or not settings.embedding_base_url.strip()


def embed_text(text: str) -> list[float]:
    """Compatibility wrapper for command-line and synchronous test callers."""
    _assert_sync_compatibility()
    if _embedding_unavailable():
        raise KnowledgeEmbeddingError("Embedding service is not configured")
    return asyncio.run(async_embed_text(text))


def _validate_embedding_vector(
    value: Any,
    *,
    expected_dimension: int | None = None,
) -> list[float]:
    if not isinstance(value, list) or not value:
        raise KnowledgeEmbeddingError("Embedding vector is missing")
    vector: list[float] = []
    for item in value:
        if isinstance(item, bool):
            raise KnowledgeEmbeddingError("Embedding vector contains an invalid value")
        try:
            number = float(item)
        except (TypeError, ValueError) as exc:
            raise KnowledgeEmbeddingError("Embedding vector contains an invalid value") from exc
        if not math.isfinite(number):
            raise KnowledgeEmbeddingError("Embedding vector contains a non-finite value")
        vector.append(number)
    if expected_dimension is not None and len(vector) != expected_dimension:
        raise KnowledgeEmbeddingError("Embedding vector dimension does not match the configured model")
    return vector


def search_knowledge(
    conn: sqlite3.Connection,
    query: str | None = None,
    *,
    question: str | None = None,
    answer: str | None = None,
    category: str | None = None,
    dimension: str | None = None,
    limit: int = 5,
    source: str | None = None,
) -> list[dict]:
    """Search internal interview reference answers with FTS5 + optional embeddings."""
    q = (question or query or "").strip()
    if not q:
        raise ValueError("Query must be non-empty")

    limit = max(1, min(limit, 20))
    category = category or dimension
    fts_rows = _search_fts(conn, q, category=category, source=source, limit=max(10, limit * 2))
    vector_rows: list[dict] = []
    embedding_unavailable = False
    try:
        vector_rows = _search_vectors(conn, q, category=category, limit=max(10, limit * 2))
    except Exception:
        embedding_unavailable = True
        if settings.require_embedding:
            raise

    merged = _compress_knowledge_results(_merge_rrf(fts_rows, vector_rows, limit))
    for item in merged:
        item["embedding_unavailable"] = embedding_unavailable
    return merged


def _search_fts(
    conn: sqlite3.Connection,
    query: str,
    *,
    category: str | None = None,
    source: str | None = None,
    limit: int = 10,
) -> list[dict]:
    sanitized = _sanitize_fts5_query(query)
    if not sanitized:
        return []
    terms = sanitized.split()
    fts_query = " OR ".join(terms) if len(terms) > 1 else sanitized

    conditions = ["knowledge_fts MATCH ?", "knowledge.kind = 'interview_qa'"]
    params: list[Any] = [fts_query]
    if category:
        conditions.append("knowledge.category = ?")
        params.append(category)
    if source:
        conditions.append("knowledge.source = ?")
        params.append(source)

    sql = f"""
        SELECT knowledge.*, rank AS score
        FROM knowledge_fts
        JOIN knowledge ON knowledge.id = knowledge_fts.rowid
        WHERE {" AND ".join(conditions)}
        ORDER BY rank
        LIMIT ?
    """
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_result(row, fts_rank=i + 1) for i, row in enumerate(rows)]


def _search_vectors(
    conn: sqlite3.Connection,
    question: str,
    *,
    category: str | None = None,
    limit: int = 10,
) -> list[dict]:
    if _embedding_unavailable():
        raise KnowledgeEmbeddingError("Embedding service is not configured")
    query_vector = _validate_embedding_vector(embed_text(question))
    rows = conn.execute(
        """
        SELECT knowledge.*, knowledge_embeddings.vector_json
        FROM knowledge_embeddings
        JOIN knowledge ON knowledge.id = knowledge_embeddings.knowledge_id
        WHERE knowledge_embeddings.embedding_model = ?
          AND knowledge.kind = 'interview_qa'
        """,
        (settings.embedding_model,),
    ).fetchall()
    if not rows:
        raise KnowledgeEmbeddingError(
            "No knowledge vectors exist for the configured embedding model; reload knowledge first"
        )
    scored = []
    for row in rows:
        if category and row["category"] != category:
            continue
        try:
            vector = _validate_embedding_vector(
                json.loads(row["vector_json"]), expected_dimension=len(query_vector)
            )
        except (TypeError, json.JSONDecodeError) as exc:
            raise KnowledgeEmbeddingError("Stored embedding vector is invalid") from exc
        scored.append((_cosine_similarity(query_vector, vector), row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        _row_to_result(row, vector_rank=i + 1, vector_score=score)
        for i, (score, row) in enumerate(scored[:limit])
    ]


def _merge_rrf(fts_rows: list[dict], vector_rows: list[dict], limit: int) -> list[dict]:
    merged: dict[int, dict] = {}
    for item in fts_rows:
        kid = int(item["id"])
        merged[kid] = item
    for item in vector_rows:
        kid = int(item["id"])
        if kid in merged:
            merged[kid].update({
                "vector_rank": item.get("vector_rank"),
                "vector_score": item.get("vector_score"),
            })
        else:
            merged[kid] = item

    for item in merged.values():
        fts_rank = item.get("fts_rank")
        vector_rank = item.get("vector_rank")
        item["rrf_score"] = (
            (1 / (60 + fts_rank) if fts_rank else 0)
            + (1 / (60 + vector_rank) if vector_rank else 0)
        )
    return sorted(merged.values(), key=lambda item: item["rrf_score"], reverse=True)[:limit]


def _truncate_retrieval_field(value: object, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def _compress_knowledge_results(results: list[dict]) -> list[dict]:
    """Bound every retrieval field before it enters a tool or diagnosis context."""
    compressed: list[dict] = []
    for item in results:
        result = dict(item)
        result["title"] = _truncate_retrieval_field(result.get("title"), 200)
        result["source"] = _truncate_retrieval_field(result.get("source"), 300)
        result["question"] = _truncate_retrieval_field(result.get("question"), 500)
        result["content"] = _truncate_retrieval_field(result.get("content"), 1200)
        result["expert_answer"] = _truncate_retrieval_field(result.get("expert_answer"), 1400)
        result["novice_answer"] = _truncate_retrieval_field(result.get("novice_answer"), 500)
        for field, max_items, item_limit in (
            ("exam_points", 6, 250),
            ("common_gaps", 5, 250),
            ("followups", 5, 250),
            ("tags", 12, 80),
        ):
            value = result.get(field)
            if isinstance(value, list):
                result[field] = [_truncate_retrieval_field(entry, item_limit) for entry in value[:max_items]]
        compressed.append(result)
    return compressed


def _row_to_result(
    row: sqlite3.Row | dict[str, Any],
    *,
    fts_rank: int | None = None,
    vector_rank: int | None = None,
    vector_score: float | None = None,
) -> dict:
    exam_points = json.loads(row["exam_points"] or "[]")
    common_gaps = json.loads(row["common_gaps"] or "[]")
    followups = json.loads(row["followups"] or "[]")
    tags = json.loads(row["tags"] or "[]")
    category = row["category"] or row["dimension"] or ""
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "source": row["source"],
        "kind": row["kind"],
        "category": category,
        "dimension": category,
        "question": row["question"],
        "novice_answer": row["novice_answer"],
        "expert_answer": row["expert_answer"],
        "exam_points": exam_points,
        "common_gaps": common_gaps,
        "followups": followups,
        "tags": tags,
        "score": row["score"] if "score" in row.keys() else vector_score,
        "fts_rank": fts_rank,
        "vector_rank": vector_rank,
        "vector_score": vector_score,
        "rrf_score": 0.0,
    }


def _sanitize_fts5_query(query: str) -> str:
    import re

    # FTS5 MATCH treats punctuation such as a trailing period as syntax. Keep
    # only word-like Latin tokens and contiguous CJK text before joining terms.
    terms = re.findall(r"[0-9A-Za-z_]+|[\u4e00-\u9fff]+", query)
    stopwords = {
        "what", "is", "the", "a", "an", "and", "or", "of", "in", "to", "for",
        "how", "does", "do", "can", "will", "would", "should", "could", "it",
        "this", "that", "are", "was", "were", "be", "been", "has", "have",
    }
    filtered = [t for t in terms if t.lower() not in stopwords]
    return " ".join(filtered)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def search_knowledge_safe(
    query: str | None = None,
    *,
    question: str | None = None,
    answer: str | None = None,
    category: str | None = None,
    dimension: str | None = None,
    limit: int = 5,
    source: str | None = None,
    trace_id: str | None = None,
) -> list[dict]:
    """Safe DB-managed search wrapper."""
    from offerpilot.core.database import get_db

    conn = None
    q = (question or query or "").strip()
    try:
        if "\n" in q:
            q = q.split("\n")[0]
        if len(q) > 300:
            q = q[:300]
        conn = get_db()
        ensure_knowledge_loaded(conn)
        results = search_knowledge(
            conn,
            question=q,
            answer=answer,
            category=category,
            dimension=dimension,
            limit=limit,
            source=source,
        )
        if trace_id:
            from offerpilot.trace.trace_eval import add_trace_event

            fts_count = len([r for r in results if r.get("fts_rank")])
            vector_count = len([r for r in results if r.get("vector_rank")])
            add_trace_event(trace_id, "knowledge_merged", 0, {
                "query": q,
                "fts_count": fts_count,
                "vector_count": vector_count,
                "count": len(results),
                "embedding_unavailable": any(r.get("embedding_unavailable") for r in results),
                "titles": [r.get("title", "") for r in results],
            })
        return results
    except Exception as e:
        if trace_id:
            try:
                from offerpilot.trace.trace_eval import add_trace_event

                add_trace_event(trace_id, "knowledge_error", 0, {"error": str(e)})
            except Exception:
                pass
        if settings.require_embedding:
            raise
        return []
    finally:
        if conn:
            conn.close()


async def search_knowledge_safe_async(
    query: str | None = None,
    *,
    question: str | None = None,
    answer: str | None = None,
    category: str | None = None,
    dimension: str | None = None,
    limit: int = 5,
    source: str | None = None,
    trace_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
    deadline: float | None = None,
) -> list[dict]:
    """Async retrieval: short SQLite work in a thread, embedding on AsyncOpenAI."""
    q = (question or query or "").strip()
    if "\n" in q:
        q = q.split("\n", 1)[0]
    q = q[:300]
    if not q:
        raise ValueError("Query must be non-empty")
    raise_if_cancelled(cancel_event)
    await ensure_knowledge_loaded_async(deadline=deadline, cancel_event=cancel_event)
    fts_rows, vector_rows = await await_with_deadline(
        asyncio.to_thread(
            _prepare_async_search, q, category or dimension, source, max(10, min(limit, 20) * 2)
        ),
        deadline=deadline,
        cancel_event=cancel_event,
    )
    embedding_unavailable = False
    try:
        if _embedding_unavailable():
            raise RuntimeError("Embedding service is not configured")
        query_vector = await async_embed_text(
            q,
            timeout=min(settings.embedding_timeout_seconds, require_remaining(deadline)),
            cancel_event=cancel_event,
            deadline=deadline,
        )
        query_vector = _validate_embedding_vector(query_vector)
        vector_results = await await_with_deadline(
            asyncio.to_thread(
                _rank_vector_rows,
                vector_rows,
                query_vector,
                category or dimension,
                max(10, min(limit, 20) * 2),
            ),
            deadline=deadline,
            cancel_event=cancel_event,
        )
    except asyncio.CancelledError:
        raise
    except RunDeadlineExceeded:
        raise
    except Exception:
        embedding_unavailable = True
        if settings.require_embedding:
            raise
        vector_results = []
    merged = _compress_knowledge_results(_merge_rrf(fts_rows, vector_results, max(1, min(limit, 20))))
    for item in merged:
        item["embedding_unavailable"] = embedding_unavailable
    if trace_id:
        await await_with_deadline(
            asyncio.to_thread(_record_async_search_trace, trace_id, q, merged),
            deadline=deadline,
            cancel_event=cancel_event,
        )
    return merged


def _prepare_async_search(
    query: str,
    category: str | None,
    source: str | None,
    limit: int,
) -> tuple[list[dict], list[dict]]:
    from offerpilot.core.database import get_db

    conn = get_db()
    try:
        fts_rows = _search_fts(conn, query, category=category, source=source, limit=limit)
        rows = conn.execute(
            """
            SELECT knowledge.*, knowledge_embeddings.vector_json
            FROM knowledge_embeddings
            JOIN knowledge ON knowledge.id = knowledge_embeddings.knowledge_id
            WHERE knowledge_embeddings.embedding_model = ?
              AND knowledge.kind = 'interview_qa'
            """,
            (settings.embedding_model,),
        ).fetchall()
        return fts_rows, [dict(row) for row in rows]
    finally:
        conn.close()


def _rank_vector_rows(
    rows: list[dict],
    query_vector: list[float],
    category: str | None,
    limit: int,
) -> list[dict]:
    if not rows:
        raise KnowledgeEmbeddingError(
            "No knowledge vectors exist for the configured embedding model; reload knowledge first"
        )
    scored: list[tuple[float, dict]] = []
    for row in rows:
        if category and row.get("category") != category:
            continue
        try:
            vector = _validate_embedding_vector(
                json.loads(row["vector_json"]), expected_dimension=len(query_vector)
            )
        except (TypeError, json.JSONDecodeError) as exc:
            raise KnowledgeEmbeddingError("Stored embedding vector is invalid") from exc
        scored.append((_cosine_similarity(query_vector, vector), row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        _row_to_result(row, vector_rank=index + 1, vector_score=score)
        for index, (score, row) in enumerate(scored[:limit])
    ]


def _record_async_search_trace(trace_id: str, query: str, results: list[dict]) -> None:
    from offerpilot.trace.trace_eval import add_trace_event

    add_trace_event(trace_id, "knowledge_merged", 0, {
        "query": query,
        "fts_count": len([item for item in results if item.get("fts_rank")]),
        "vector_count": len([item for item in results if item.get("vector_rank")]),
        "count": len(results),
        "embedding_unavailable": any(item.get("embedding_unavailable") for item in results),
        "titles": [item.get("title", "") for item in results],
    })


async def ensure_knowledge_loaded_async(
    *,
    deadline: float | None = None,
    cancel_event: asyncio.Event | None = None,
) -> int:
    """Load the initial index asynchronously when the database is empty."""
    count = await await_with_deadline(
        asyncio.to_thread(_knowledge_count), deadline=deadline, cancel_event=cancel_event
    )
    if count == 0:
        result = await import_knowledge_with_stats_async(
            deadline=deadline, cancel_event=cancel_event
        )
        return int(result["imported"])
    return count


def _knowledge_count() -> int:
    from offerpilot.core.database import get_db

    conn = get_db()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0])
    finally:
        conn.close()


def ensure_knowledge_loaded(conn: sqlite3.Connection) -> int:
    """Compatibility entry point for synchronous tests and eval CLI callers."""
    _ = conn
    _assert_sync_compatibility()
    return asyncio.run(ensure_knowledge_loaded_async())
