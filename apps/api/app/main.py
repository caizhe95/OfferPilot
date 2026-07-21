"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import get_db, init_db
from app.core.errors import (
    AppError,
    app_error_handler,
    general_exception_handler,
    http_exception_handler,
)
from app.knowledge.knowledge_importer import ensure_knowledge_loaded

from app.audio.audio_api import router as audio_router
from app.chat.chat_api import router as chat_router
from app.diagnosis.diagnose_api import router as diagnose_router
from app.knowledge.knowledge_api import router as knowledge_router
from app.permission.permission_api import alias_router as permission_alias_router
from app.permission.permission_api import router as permission_router
from app.reports.reports_api import router as reports_router
from app.session.session_api import router as session_router
from app.skills.skills_api import router as skills_router
from app.tools.tools_api import router as tools_api_router
from app.trace.trace_eval_api import router as trace_eval_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize DB and load knowledge on startup."""
    init_db()
    conn = None
    try:
        conn = get_db()
        ensure_knowledge_loaded(conn)
    finally:
        if conn:
            conn.close()
    yield


app = FastAPI(
    title="OfferPilot Lite API",
    description="单 Agent 高工程版 OfferPilot 后端",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Error handlers
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}


# Register routers
app.include_router(knowledge_router)
app.include_router(session_router)
app.include_router(permission_router)
app.include_router(permission_alias_router)
app.include_router(skills_router)
app.include_router(trace_eval_router)
app.include_router(audio_router)
app.include_router(chat_router)
app.include_router(diagnose_router)
app.include_router(tools_api_router)
app.include_router(reports_router)
