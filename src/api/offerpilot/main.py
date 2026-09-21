"""OfferPilot v4 FastAPI application."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from offerpilot.core.config import settings
from offerpilot.core.logging import bind_context, configure_logging, log_event, reset_context, request_id_from_header
from offerpilot.database.connection import get_db, init_db
from offerpilot.core.errors import AppError, app_error_handler, general_exception_handler, http_exception_handler, is_database_busy
from offerpilot.core.security import is_trusted_origin
from offerpilot.profiles.api import router as profile_router
from offerpilot.sessions.api import router as session_router
from offerpilot.audio.storage import cleanup_stale_uploads, expire_audio_for_approvals
from offerpilot.diagnosis.reporting import rules_directory
from offerpilot.knowledge.api import admin_router as knowledge_admin_router
from offerpilot.knowledge.indexer import ensure_knowledge_loaded_async
from offerpilot.runs.api import router as run_router
from offerpilot.runs.admin_api import router as run_admin_router
from offerpilot.runs.service import run_service
from offerpilot.runs.recovery import expire_approvals, list_pending_runs, list_resumable_runs, recover_runs
from offerpilot.approvals.api import router as approval_router
from offerpilot.audio.api import router as audio_router
from offerpilot.evaluations.api import router as eval_router

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings.validate_runtime_security()
    if not settings.resolved_knowledge_dir.is_dir():
        raise RuntimeError(f"Knowledge directory is unavailable: {settings.resolved_knowledge_dir}")
    if not rules_directory().is_dir():
        raise RuntimeError(f"Harness rules directory is unavailable: {rules_directory()}")
    init_db()
    await ensure_knowledge_loaded_async()
    recovered = recover_runs()
    scheduled = [*list_pending_runs(), *list_resumable_runs()]
    for run_id in dict.fromkeys(scheduled):
        run_service.start(run_id)
    log_event(logger, logging.INFO, "app_started", count=len(recovered))

    async def periodic_cleanup() -> None:
        while True:
            await asyncio.sleep(300)
            expired = expire_approvals()
            expired_audio = expire_audio_for_approvals(expired)
            stale_uploads = cleanup_stale_uploads()
            log_event(logger, logging.INFO, "runtime_cleanup_completed", count=len(expired) + expired_audio + stale_uploads)

    task = asyncio.create_task(periodic_cleanup(), name="offerpilot-runtime-cleanup")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        log_event(logger, logging.INFO, "app_stopped")


app = FastAPI(title="OfferPilot API", description="技术面试训练 Session Run 服务", version="4.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)


@app.middleware("http")
async def enforce_write_origin(request: Request, call_next):
    request_id = request_id_from_header(request.headers.get("X-Request-ID"))
    tokens = bind_context(request_id=request_id)
    started = perf_counter()
    status_code = 500
    try:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not is_trusted_origin(request.headers.get("origin")):
            status_code = 403
            return JSONResponse(status_code=403, content={"error": {"code": "HTTP_403", "message": "Untrusted Origin"}}, headers={"X-Request-ID": request_id})
        try:
            response = await call_next(request)
        except Exception as exc:
            if not is_database_busy(exc):
                raise
            response = await general_exception_handler(request, exc)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        route = getattr(request.scope.get("route"), "path", request.url.path)
        level = logging.DEBUG if route == "/health" else logging.INFO
        log_event(logger, level, "request_completed", method=request.method, route=route, status_code=status_code, duration_ms=round((perf_counter() - started) * 1000, 1))
        reset_context(tokens)


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "4.0.0"}


@app.get("/ready")
async def readiness_check():
    conn = get_db()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return {"status": "ready", "version": "4.0.0"}


app.include_router(profile_router)
app.include_router(knowledge_admin_router)
app.include_router(eval_router)
app.include_router(run_admin_router)
app.include_router(session_router)
app.include_router(run_router)
app.include_router(approval_router)
app.include_router(audio_router)
