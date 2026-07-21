"""Knowledge search API endpoints."""

from fastapi import APIRouter, Query, HTTPException, Header
from pydantic import BaseModel
from app.core.database import get_db
from app.knowledge.knowledge_importer import search_knowledge, ensure_knowledge_loaded, get_index_status
from app.core.config import settings

router = APIRouter(prefix="/api/tools", tags=["tools"])
admin_router = APIRouter(prefix="/api/admin/knowledge", tags=["admin"])


class SearchKnowledgeResponse(BaseModel):
    query: str
    results: list[dict]
    total: int


class SearchKnowledgeRequest(BaseModel):
    question: str | None = None
    query: str | None = None
    answer: str | None = None
    category: str | None = None
    dimension: str | None = None
    limit: int = 5
    source: str | None = None


@router.get("/search-knowledge", response_model=SearchKnowledgeResponse)
async def search_knowledge_endpoint(
    query: str = Query(..., min_length=1, description="Interview question query"),
    category: str | None = Query(None, description="Optional category filter"),
    dimension: str | None = Query(None, description="Deprecated category alias"),
    limit: int = Query(5, ge=1, le=20, description="Max results (1-20)"),
    source: str | None = Query(None, description="Optional source filter"),
):
    """Internal tool API: search interview reference answers for diagnosis.

    This is not a user-facing knowledge Q&A endpoint.
    """
    if not query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")

    conn = get_db()
    try:
        ensure_knowledge_loaded(conn)
        results = search_knowledge(
            conn,
            question=query.strip(),
            category=category or dimension,
            limit=limit,
            source=source,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()

    return SearchKnowledgeResponse(
        query=query,
        results=results,
        total=len(results),
    )


@router.post("/search-knowledge", response_model=SearchKnowledgeResponse)
async def search_knowledge_post_endpoint(request: SearchKnowledgeRequest):
    """Internal JSON tool call endpoint for Agent/FastAPI diagnosis paths."""
    question = (request.question or request.query or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Query must not be empty")
    limit = max(1, min(request.limit, 20))
    conn = get_db()
    try:
        ensure_knowledge_loaded(conn)
        results = search_knowledge(
            conn,
            question=question,
            answer=request.answer,
            category=request.category or request.dimension,
            limit=limit,
            source=request.source,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
    return SearchKnowledgeResponse(query=question, results=results, total=len(results))


def _require_admin_key(value: str | None) -> None:
    if not settings.admin_key or value != settings.admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


@admin_router.get("/status")
async def index_status(x_offerpilot_admin_key: str | None = Header(default=None)):
    _require_admin_key(x_offerpilot_admin_key)
    conn = get_db()
    try:
        return get_index_status(conn)
    finally:
        conn.close()


@admin_router.post("/reindex")
async def reload_knowledge(x_offerpilot_admin_key: str | None = Header(default=None)):
    """Explicitly rebuild FTS and embedding indexes using current configuration."""
    _require_admin_key(x_offerpilot_admin_key)
    conn = get_db()
    try:
        from app.knowledge.knowledge_importer import import_knowledge_with_stats
        result = import_knowledge_with_stats(conn)
    finally:
        conn.close()
    return result
