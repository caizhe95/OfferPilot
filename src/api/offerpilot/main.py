"""FastAPI application entry point."""

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from offerpilot.core.config import settings
from offerpilot.core.database import init_db
from offerpilot.core.errors import (
    AppError,
    app_error_handler,
    general_exception_handler,
    http_exception_handler,
)
from offerpilot.knowledge.knowledge_importer import ensure_knowledge_loaded_async

from offerpilot.audio.audio import expire_audio_for_approvals, expire_legacy_audio_approvals
from offerpilot.audio.audio_api import router as audio_router
from offerpilot.coaching.coaching_api import router as coaching_router
from offerpilot.coaching.state import sweep_runtime_state
from offerpilot.knowledge.knowledge_api import admin_router as knowledge_admin_router
from offerpilot.permission.permission_api import router as permission_router
from offerpilot.session.session_api import router as session_router
from offerpilot.trace.trace_eval_api import router as trace_eval_router
from offerpilot.core.profile import is_trusted_origin
from offerpilot.core.profile_api import router as profile_router
from offerpilot.harness.harness import rules_directory


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize DB and load knowledge on startup."""
    settings.validate_runtime_security()
    if not settings.resolved_knowledge_dir.is_dir():
        raise RuntimeError(f"Knowledge directory is unavailable: {settings.resolved_knowledge_dir}")
    if not rules_directory().is_dir():
        raise RuntimeError(f"Harness rules directory is unavailable: {rules_directory()}")
    init_db()
    expire_legacy_audio_approvals()
    initial_cleanup = sweep_runtime_state()
    expire_audio_for_approvals(initial_cleanup["expired_approvals"])
    await ensure_knowledge_loaded_async()
    async def periodic_cleanup() -> None:
        while True:
            await asyncio.sleep(300)
            cleanup = sweep_runtime_state()
            expire_audio_for_approvals(cleanup["expired_approvals"])

    cleanup_task = asyncio.create_task(periodic_cleanup(), name="offerpilot-runtime-cleanup")
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(
    title="OfferPilot Lite API",
    description="单 Agent 高工程版 OfferPilot 后端",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Error handlers
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)


@app.middleware("http")
async def enforce_write_origin(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not is_trusted_origin(request.headers.get("origin")):
            return JSONResponse(status_code=403, content={"error": {"code": "HTTP_403", "message": "Untrusted Origin"}})
    return await call_next(request)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}


# Register routers
app.include_router(profile_router)
app.include_router(knowledge_admin_router)
app.include_router(session_router)
app.include_router(permission_router)
app.include_router(trace_eval_router)
app.include_router(audio_router)
app.include_router(coaching_router)
