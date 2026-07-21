"""Knowledge importer and FTS5/embedding retrieval."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.knowledge.knowledge_parser import parse_directory


def _knowledge_dir(knowledge_dir: Path | None = None) -> Path:
    if knowledge_dir is not None:
        return knowledge_dir
    return settings.resolved_knowledge_dir


def get_index_status(conn: sqlite3.Connection) -> dict[str, Any]:
    knowledge_count = int(conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0])
    embedding_count = int(conn.execute("SELECT COUNT(*) FROM knowledge_embeddings WHERE embedding_model = ?", (settings.embedding_model,)).fetchone()[0])
    return {"knowledge_count": knowledge_count, "embedding_count": embedding_count, "embedding_model": settings.embedding_model, "needs_reindex": knowledge_count != embedding_count}


def import_knowledge(conn: sqlite3.Connection, knowledge_dir: Path | None = None) -> int:
    """Import all knowledge files and rebuild FTS5/embedding rows."""
    result = import_knowledge_with_stats(conn, knowledge_dir)
    return int(result["imported"])


def import_knowledge_with_stats(conn: sqlite3.Connection, knowledge_dir: Path | None = None) -> dict:
    """Import knowledge and return reload stats."""
    entries = parse_directory(_knowledge_dir(knowledge_dir))
    if not entries:
        return {"status": "ok", "imported": 0, "embedded": 0, "reused": 0, "embedding_unavailable": True, "errors": []}

    embedding_cache = _load_embedding_cache(conn)
    conn.execute("DELETE FROM knowledge_fts")
    conn.execute("DELETE FROM knowledge_embeddings")
    conn.execute("DELETE FROM knowledge")

    imported = 0
    embedded = 0
    reused = 0
    errors: list[dict] = []
    embedding_unavailable = _embedding_unavailable()

    for entry in entries:
        try:
            row_id = _insert_knowledge(conn, entry)
            _insert_fts(conn, row_id, entry)
            imported += 1
            if not embedding_unavailable:
                text = _embedding_text(entry)
                content_hash = _content_hash(text)
                vector_json = embedding_cache.get((settings.embedding_model, content_hash))
                if vector_json is None:
                    vector_json = json.dumps(embed_text(text))
                    embedded += 1
                else:
                    reused += 1
                _insert_embedding(conn, row_id, vector_json, content_hash)
        except Exception as exc:
            errors.append({"source": entry.get("source", ""), "error": str(exc)})

    conn.commit()
    return {
        "status": "ok",
        "imported": imported,
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
    """Call an OpenAI-compatible embeddings endpoint."""
    if _embedding_unavailable():
        raise RuntimeError("Embedding service is not configured")
    from openai import OpenAI

    client = OpenAI(
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        timeout=settings.embedding_timeout_seconds,
        max_retries=0,
    )
    response = client.embeddings.create(model=settings.embedding_model, input=text)
    return [float(v) for v in response.data[0].embedding]


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

    merged = _merge_rrf(fts_rows, vector_rows, limit)
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
        raise RuntimeError("Embedding service is not configured")
    query_vector = embed_text(question)
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
        raise RuntimeError("No knowledge vectors exist for the configured embedding model; reload knowledge first")
    scored = []
    for row in rows:
        if category and row["category"] != category:
            continue
        vector = json.loads(row["vector_json"])
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


def _row_to_result(
    row: sqlite3.Row,
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

    sanitized = re.sub(r'[*"():?!\-，。？！（）【】]', ' ', query)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    stopwords = {
        "what", "is", "the", "a", "an", "and", "or", "of", "in", "to", "for",
        "how", "does", "do", "can", "will", "would", "should", "could", "it",
        "this", "that", "are", "was", "were", "be", "been", "has", "have",
    }
    terms = sanitized.split()
    filtered = [t for t in terms if t.lower() not in stopwords]
    return " ".join(filtered) if filtered else sanitized


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
    from app.core.database import get_db

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
            from app.trace.trace_eval import add_trace_event

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
                from app.trace.trace_eval import add_trace_event

                add_trace_event(trace_id, "knowledge_error", 0, {"error": str(e)})
            except Exception:
                pass
        if settings.require_embedding:
            raise
        return []
    finally:
        if conn:
            conn.close()


def ensure_knowledge_loaded(conn: sqlite3.Connection) -> int:
    """Load knowledge if empty, otherwise return current count."""
    count = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
    if count == 0:
        return import_knowledge(conn)
    return count
