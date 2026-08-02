"""Admin-only Knowledge index endpoints.

Business retrieval is an in-process Coach/Diagnosis tool and is intentionally not
available through an anonymous HTTP route.
"""

from fastapi import APIRouter, Depends
import asyncio

from offerpilot.core.database import get_db
from offerpilot.knowledge.knowledge_importer import get_index_status, import_knowledge_with_stats_async
from offerpilot.core.profile import require_admin_key

admin_router = APIRouter(prefix="/api/admin/knowledge", tags=["admin"])

@admin_router.get("/status")
async def index_status(_: None = Depends(require_admin_key)):
    return await asyncio.to_thread(_read_index_status)


def _read_index_status() -> dict:
    conn = get_db()
    try:
        return get_index_status(conn)
    finally:
        conn.close()


@admin_router.post("/reindex")
async def reload_knowledge(_: None = Depends(require_admin_key)):
    """Explicitly rebuild FTS and embedding indexes using current configuration."""
    return await import_knowledge_with_stats_async()
