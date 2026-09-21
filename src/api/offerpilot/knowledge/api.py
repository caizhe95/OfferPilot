"""Admin knowledge indexing endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from offerpilot.core.security import require_admin_key
from offerpilot.knowledge.indexer import get_index_status, import_knowledge_with_stats_async

admin_router = APIRouter(prefix="/api/admin/knowledge", tags=["admin-knowledge"])


@admin_router.post("/reindex")
async def reindex_knowledge(_: None = Depends(require_admin_key)):
    return await import_knowledge_with_stats_async()


@admin_router.get("/status")
async def knowledge_status(_: None = Depends(require_admin_key)):
    return await asyncio.to_thread(get_index_status)
