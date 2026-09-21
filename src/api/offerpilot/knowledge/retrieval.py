"""Asynchronous hybrid retrieval over the local FTS5 and embedding indexes."""

from __future__ import annotations

import asyncio
import json
import math
import re
import sqlite3
from typing import Any

from offerpilot.core.config import settings
from offerpilot.core.deadlines import RunDeadlineExceeded, await_with_deadline, raise_if_cancelled, require_remaining
from offerpilot.database.connection import get_db
from offerpilot.knowledge.indexer import KnowledgeEmbeddingError, _embedding_unavailable, _validate_embedding_vector, ensure_knowledge_loaded_async
from offerpilot.llm.embeddings import embed_text


async def search_knowledge(
    query: str | None = None,
    *,
    question: str | None = None,
    answer: str | None = None,
    category: str | None = None,
    dimension: str | None = None,
    limit: int = 5,
    source: str | None = None,
    cancel_event: asyncio.Event | None = None,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Retrieve bounded results without coupling to a Run or HTTP endpoint."""
    q = (question or query or "").strip()
    if "\n" in q:
        q = q.split("\n", 1)[0]
    q = q[:300]
    if not q:
        raise ValueError("Query must be non-empty")
    raise_if_cancelled(cancel_event)
    await ensure_knowledge_loaded_async(deadline=deadline, cancel_event=cancel_event)
    bounded_limit = max(1, min(limit, 20))
    category = category or dimension
    fts_rows, vector_rows = await await_with_deadline(
        asyncio.to_thread(_prepare_search, q, category, source, max(10, bounded_limit * 2)),
        deadline=deadline,
        cancel_event=cancel_event,
    )
    embedding_unavailable = False
    try:
        if _embedding_unavailable():
            raise RuntimeError("Embedding service is not configured")
        query_vector = await embed_text(
            q,
            timeout=min(settings.embedding_timeout_seconds, require_remaining(deadline)),
            cancel_event=cancel_event,
            deadline=deadline,
        )
        query_vector = _validate_embedding_vector(query_vector)
        vector_results = await await_with_deadline(
            asyncio.to_thread(_rank_vector_rows, vector_rows, query_vector, category, max(10, bounded_limit * 2)),
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
    merged = _compress_results(_merge_rrf(fts_rows, vector_results, bounded_limit))
    await await_with_deadline(asyncio.to_thread(_attach_exam_point_refs, merged), deadline=deadline, cancel_event=cancel_event)
    for item in merged:
        item["embedding_unavailable"] = embedding_unavailable
    return merged


def _prepare_search(query: str, category: str | None, source: str | None, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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


def _search_fts(conn: sqlite3.Connection, query: str, *, category: str | None, source: str | None, limit: int) -> list[dict[str, Any]]:
    sanitized = _sanitize_fts5_query(query)
    if not sanitized:
        return []
    terms = sanitized.split()
    conditions = ["knowledge_fts MATCH ?", "knowledge.kind = 'interview_qa'"]
    params: list[Any] = [" OR ".join(terms) if len(terms) > 1 else sanitized]
    if category:
        conditions.append("knowledge.category = ?")
        params.append(category)
    if source:
        conditions.append("knowledge.source = ?")
        params.append(source)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT knowledge.*, rank AS score
        FROM knowledge_fts JOIN knowledge ON knowledge.id = knowledge_fts.rowid
        WHERE {' AND '.join(conditions)}
        ORDER BY rank LIMIT ?
        """,
        params,
    ).fetchall()
    return [_row_to_result(row, fts_rank=index + 1) for index, row in enumerate(rows)]


def _rank_vector_rows(rows: list[dict[str, Any]], query_vector: list[float], category: str | None, limit: int) -> list[dict[str, Any]]:
    if not rows:
        raise KnowledgeEmbeddingError("No knowledge vectors exist for the configured embedding model; reload knowledge first")
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        if category and row.get("category") != category:
            continue
        try:
            vector = _validate_embedding_vector(json.loads(row["vector_json"]), expected_dimension=len(query_vector))
        except (TypeError, json.JSONDecodeError) as exc:
            raise KnowledgeEmbeddingError("Stored embedding vector is invalid") from exc
        scored.append((_cosine_similarity(query_vector, vector), row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [_row_to_result(row, vector_rank=index + 1, vector_score=score) for index, (score, row) in enumerate(scored[:limit])]


def _merge_rrf(fts_rows: list[dict[str, Any]], vector_rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    merged = {int(item["id"]): dict(item) for item in fts_rows}
    for item in vector_rows:
        existing = merged.get(int(item["id"]))
        if existing is None:
            merged[int(item["id"])] = dict(item)
        else:
            existing.update({"vector_rank": item.get("vector_rank"), "vector_score": item.get("vector_score")})
    for item in merged.values():
        item["rrf_score"] = (1 / (60 + item["fts_rank"]) if item.get("fts_rank") else 0) + (1 / (60 + item["vector_rank"]) if item.get("vector_rank") else 0)
    return sorted(merged.values(), key=lambda item: item["rrf_score"], reverse=True)[:limit]


def _compress_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    limits = {"title": 200, "source": 300, "question": 500, "content": 1200, "expert_answer": 1400, "novice_answer": 500}
    compressed: list[dict[str, Any]] = []
    for item in results:
        result = dict(item)
        for field, limit in limits.items():
            result[field] = _truncate(result.get(field), limit)
        for field, max_items, item_limit in (("exam_points", 6, 250), ("common_gaps", 5, 250), ("followups", 5, 250), ("tags", 12, 80)):
            if isinstance(result.get(field), list):
                result[field] = [_truncate(value, item_limit) for value in result[field][:max_items]]
        compressed.append(result)
    return compressed


def _attach_exam_point_refs(results: list[dict[str, Any]]) -> None:
    ids = [int(item["id"]) for item in results if isinstance(item.get("id"), int)]
    if not ids:
        return
    conn = get_db()
    try:
        rows = conn.execute(
            f"SELECT id, knowledge_id, label FROM knowledge_exam_points WHERE knowledge_id IN ({','.join('?' for _ in ids)}) ORDER BY knowledge_id, source_key",
            ids,
        ).fetchall()
    finally:
        conn.close()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["knowledge_id"]), []).append({"id": str(row["id"]), "label": str(row["label"]), "knowledge_id": int(row["knowledge_id"])})
    for item in results:
        item["exam_point_refs"] = grouped.get(int(item["id"]), [])


def _row_to_result(row: sqlite3.Row | dict[str, Any], *, fts_rank: int | None = None, vector_rank: int | None = None, vector_score: float | None = None) -> dict[str, Any]:
    category = row["category"] or row["dimension"] or ""
    return {
        "id": row["id"], "title": row["title"], "content": row["content"], "source": row["source"],
        "kind": row["kind"], "category": category, "dimension": category, "question": row["question"],
        "novice_answer": row["novice_answer"], "expert_answer": row["expert_answer"],
        "exam_points": json.loads(row["exam_points"] or "[]"), "common_gaps": json.loads(row["common_gaps"] or "[]"),
        "followups": json.loads(row["followups"] or "[]"), "tags": json.loads(row["tags"] or "[]"),
        "score": row["score"] if "score" in row.keys() else vector_score,
        "fts_rank": fts_rank, "vector_rank": vector_rank, "vector_score": vector_score, "rrf_score": 0.0,
    }


def _sanitize_fts5_query(query: str) -> str:
    terms = re.findall(r"[0-9A-Za-z_]+|[\u4e00-\u9fff]+", query)
    stopwords = {"what", "is", "the", "a", "an", "and", "or", "of", "in", "to", "for", "how", "does", "do", "can", "will", "would", "should", "could", "it", "this", "that", "are", "was", "were", "be", "been", "has", "have"}
    return " ".join(term for term in terms if term.lower() not in stopwords)


def _cosine_similarity(first: list[float], second: list[float]) -> float:
    if not first or not second or len(first) != len(second):
        return 0.0
    first_norm = math.sqrt(sum(value * value for value in first))
    second_norm = math.sqrt(sum(value * value for value in second))
    return 0.0 if not first_norm or not second_norm else sum(left * right for left, right in zip(first, second)) / (first_norm * second_norm)


def _truncate(value: object, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...(truncated)"
