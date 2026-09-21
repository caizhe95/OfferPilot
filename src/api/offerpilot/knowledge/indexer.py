"""Knowledge parsing, embedding validation, and atomic index rebuilds."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from offerpilot.core.config import settings
from offerpilot.core.deadlines import await_with_deadline, raise_if_cancelled, require_remaining
from offerpilot.database.connection import get_db
from offerpilot.knowledge.parser import parse_directory
from offerpilot.llm.embeddings import embed_text


class KnowledgeEmbeddingError(RuntimeError):
    """The configured embedding contract cannot be satisfied."""


@dataclass(frozen=True)
class _PreparedKnowledgeEntry:
    entry: dict[str, Any]
    content_hash: str
    vector_json: str | None


def get_index_status() -> dict[str, Any]:
    """Read index metadata without exposing SQLite connections to the HTTP layer."""
    conn = get_db()
    try:
        knowledge_count = int(conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0])
        embedding_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM knowledge_embeddings WHERE embedding_model = ?",
                (settings.embedding_model,),
            ).fetchone()[0]
        )
        return {
            "knowledge_count": knowledge_count,
            "embedding_count": embedding_count,
            "embedding_model": settings.embedding_model,
            "needs_reindex": knowledge_count != embedding_count,
        }
    finally:
        conn.close()


async def import_knowledge_with_stats_async(
    knowledge_dir: Path | None = None,
    *,
    deadline: float | None = None,
    cancel_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Prepare the corpus first, then replace FTS and vectors atomically."""
    entries = await await_with_deadline(asyncio.to_thread(parse_directory, knowledge_dir or settings.resolved_knowledge_dir), deadline=deadline, cancel_event=cancel_event)
    if not entries:
        return {"status": "ok", "imported": 0, "embedded": 0, "reused": 0, "embedding_unavailable": _embedding_unavailable(), "errors": []}
    _validate_exam_point_ids(entries)
    embedding_unavailable = _embedding_unavailable()
    if settings.require_embedding and embedding_unavailable:
        raise KnowledgeEmbeddingError("Embedding service is required but not configured")
    cache = await await_with_deadline(asyncio.to_thread(_load_embedding_cache), deadline=deadline, cancel_event=cancel_event)
    expected_dimension: int | None = None
    prepared: list[_PreparedKnowledgeEntry] = []
    embedded = reused = 0
    errors: list[dict[str, str]] = []
    for entry in entries:
        raise_if_cancelled(cancel_event)
        require_remaining(deadline)
        content_hash = hashlib.sha256(_embedding_text(entry).encode("utf-8")).hexdigest()
        vector_json: str | None = None
        if not embedding_unavailable:
            try:
                cached = cache.get((settings.embedding_model, content_hash))
                vector = _validate_embedding_vector(json.loads(cached) if cached else await embed_text(_embedding_text(entry), timeout=min(settings.embedding_timeout_seconds, require_remaining(deadline)), cancel_event=cancel_event, deadline=deadline), expected_dimension=expected_dimension)
                vector_json = json.dumps(vector, ensure_ascii=True, separators=(",", ":"))
                expected_dimension = len(vector)
                if cached:
                    reused += 1
                else:
                    embedded += 1
                    cache[(settings.embedding_model, content_hash)] = vector_json
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if settings.require_embedding:
                    raise KnowledgeEmbeddingError("Required embedding generation or validation failed; existing index was preserved") from exc
                embedding_unavailable = True
                prepared = [_PreparedKnowledgeEntry(item.entry, item.content_hash, None) for item in prepared]
                embedded = reused = 0
                errors.append({"source": str(entry.get("source", "")), "error": "embedding_unavailable"})
        prepared.append(_PreparedKnowledgeEntry(entry, content_hash, vector_json))
    if settings.require_embedding and any(item.vector_json is None for item in prepared):
        raise KnowledgeEmbeddingError("Required embedding is missing; existing index was preserved")
    await await_with_deadline(asyncio.to_thread(_replace_index, prepared), deadline=deadline, cancel_event=cancel_event)
    return {"status": "ok", "imported": len(prepared), "embedded": embedded, "reused": reused, "embedding_unavailable": embedding_unavailable, "errors": errors}


async def ensure_knowledge_loaded_async(*, deadline: float | None = None, cancel_event: asyncio.Event | None = None) -> int:
    count = await await_with_deadline(asyncio.to_thread(_knowledge_count), deadline=deadline, cancel_event=cancel_event)
    if count:
        return count
    result = await import_knowledge_with_stats_async(deadline=deadline, cancel_event=cancel_event)
    return int(result["imported"])


def _embedding_unavailable() -> bool:
    return not settings.embedding_api_key.strip() or not settings.embedding_base_url.strip()


def _validate_embedding_vector(value: Any, *, expected_dimension: int | None = None) -> list[float]:
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


def _validate_exam_point_ids(entries: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for entry in entries:
        points, ids = entry.get("exam_points", []), entry.get("exam_point_ids", [])
        if not points and not ids:
            continue
        if not isinstance(points, list) or not isinstance(ids, list) or len(points) != len(ids):
            raise ValueError(f"Knowledge entry has missing exam point IDs: {entry.get('source', '')}")
        for point_id in ids:
            if not isinstance(point_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,79}", point_id):
                raise ValueError(f"Invalid exam point ID: {point_id}")
            if point_id in seen:
                raise ValueError(f"Duplicate exam point ID: {point_id}")
            seen.add(point_id)


def _embedding_text(entry: dict[str, Any]) -> str:
    return "\n".join([str(entry.get("title", "")), str(entry.get("question", "")), str(entry.get("expert_answer", "")), "\n".join(entry.get("exam_points", [])), "\n".join(entry.get("common_gaps", [])), " ".join(entry.get("tags", []))]).strip()


def _load_embedding_cache() -> dict[tuple[str, str], str]:
    conn = get_db()
    try:
        return {(str(row["embedding_model"]), str(row["content_hash"])): str(row["vector_json"]) for row in conn.execute("SELECT embedding_model, content_hash, vector_json FROM knowledge_embeddings").fetchall()}
    finally:
        conn.close()


def _replace_index(prepared: list[_PreparedKnowledgeEntry]) -> None:
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in ("knowledge_fts", "knowledge_embeddings", "knowledge_exam_points", "knowledge"):
            conn.execute(f"DELETE FROM {table}")
        for item in prepared:
            entry = item.entry
            row_id = conn.execute("""INSERT INTO knowledge (title, content, source, dimension, kind, category, question, novice_answer, expert_answer, exam_points, common_gaps, followups, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (entry.get("title", ""), entry.get("content", ""), entry.get("source", ""), entry.get("category", entry.get("dimension", "")), entry.get("kind", "interview_qa"), entry.get("category", entry.get("dimension", "")), entry.get("question", ""), entry.get("novice_answer", ""), entry.get("expert_answer", ""), json.dumps(entry.get("exam_points", []), ensure_ascii=False), json.dumps(entry.get("common_gaps", []), ensure_ascii=False), json.dumps(entry.get("followups", []), ensure_ascii=False), json.dumps(entry.get("tags", []), ensure_ascii=False))).lastrowid
            if row_id is None:
                raise RuntimeError("knowledge insert did not return a row id")
            conn.execute("""INSERT INTO knowledge_fts (rowid, title, question, expert_answer, novice_answer, exam_points, common_gaps, tags, category, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (row_id, entry.get("title", ""), entry.get("question", ""), entry.get("expert_answer", ""), entry.get("novice_answer", ""), "\n".join(entry.get("exam_points", [])), "\n".join(entry.get("common_gaps", [])), " ".join(entry.get("tags", [])), entry.get("category", entry.get("dimension", "")), entry.get("source", "")))
            timestamp = datetime.now(timezone.utc).isoformat()
            for point_id, label in zip(entry.get("exam_point_ids", []), entry.get("exam_points", [])):
                conn.execute("INSERT INTO knowledge_exam_points(id, knowledge_id, label, aliases, source_key, updated_at) VALUES(?, ?, ?, '[]', ?, ?)", (point_id, row_id, str(label).strip(), point_id, timestamp))
            if item.vector_json is not None:
                conn.execute("INSERT INTO knowledge_embeddings (knowledge_id, embedding_model, vector_json, content_hash, created_at) VALUES (?, ?, ?, ?, ?)", (row_id, settings.embedding_model, item.vector_json, item.content_hash, timestamp))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _knowledge_count() -> int:
    conn = get_db()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0])
    finally:
        conn.close()
