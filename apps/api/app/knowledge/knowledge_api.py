"""Knowledge search API endpoints."""

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from app.core.database import get_db
from app.knowledge.knowledge_importer import search_knowledge, ensure_knowledge_loaded

router = APIRouter(prefix="/api/tools", tags=["tools"])


class SearchKnowledgeResponse(BaseModel):
    query: str
    results: list[dict]
    total: int


class SearchKnowledgeRequest(BaseModel):
    query: str
    dimension: str | None = None
    limit: int = 5
    source: str | None = None


@router.get("/search-knowledge", response_model=SearchKnowledgeResponse)
async def search_knowledge_endpoint(
    query: str = Query(..., min_length=1, description="Search query"),
    dimension: str | None = Query(None, description="Optional dimension filter"),
    limit: int = Query(5, ge=1, le=20, description="Max results (1-20)"),
    source: str | None = Query(None, description="Optional source filter"),
):
    """Search knowledge base using FTS5 full-text search.

    Returns matching entries with title, content, dimension, source, and relevance score.
    """
    if not query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")

    conn = get_db()
    try:
        ensure_knowledge_loaded(conn)
        results = search_knowledge(
            conn,
            query=query.strip(),
            dimension=dimension,
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
    """POST alias for agents/clients that prefer JSON tool calls."""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")
    limit = max(1, min(request.limit, 20))
    conn = get_db()
    try:
        ensure_knowledge_loaded(conn)
        results = search_knowledge(
            conn,
            query=request.query.strip(),
            dimension=request.dimension,
            limit=limit,
            source=request.source,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
    return SearchKnowledgeResponse(query=request.query, results=results, total=len(results))


@router.post("/knowledge/reload")
async def reload_knowledge():
    """Reload knowledge from the knowledge directory."""
    conn = get_db()
    try:
        from app.knowledge.knowledge_importer import import_knowledge
        count = import_knowledge(conn)
    finally:
        conn.close()
    return {"status": "ok", "count": count}
