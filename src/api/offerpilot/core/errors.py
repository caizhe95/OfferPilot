"""Unified error response models."""

import logging
import sqlite3

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi import Request
from offerpilot.core.logging import log_event, log_exception

logger = logging.getLogger(__name__)


def is_database_busy(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).lower()
        for marker in ("database is locked", "database is busy")
    )


class AppError(Exception):
    """Base application error."""

    def __init__(self, message: str, code: str = "INTERNAL_ERROR", status_code: int = 500):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, code="NOT_FOUND", status_code=404)


class ValidationError(AppError):
    def __init__(self, message: str = "Validation failed"):
        super().__init__(message, code="VALIDATION_ERROR", status_code=400)


class PermissionError(AppError):
    def __init__(self, message: str = "Permission denied"):
        super().__init__(message, code="PERMISSION_ERROR", status_code=403)


def error_response(code: str, message: str, status_code: int = 500) -> JSONResponse:
    """Create a standardized error JSON response."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
            }
        },
    )


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, AppError):
        if exc.status_code >= 500:
            log_exception(logger, "app_error", exc, include_stack=True, error_code=exc.code, status_code=exc.status_code)
        else:
            log_event(logger, logging.INFO, "app_error", error_code=exc.code, status_code=exc.status_code)
        return error_response(exc.code, exc.message, exc.status_code)
    log_exception(logger, "internal_error", exc, include_stack=True, error_code="INTERNAL_ERROR", status_code=500)
    return error_response("INTERNAL_ERROR", "An unexpected error occurred", 500)


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        level = logging.WARNING if exc.status_code in {401, 403} else logging.INFO
        log_event(logger, level, "http_error", error_code=f"HTTP_{exc.status_code}", status_code=exc.status_code)
        return error_response(
            code=f"HTTP_{exc.status_code}",
            message=str(exc.detail),
            status_code=exc.status_code,
        )
    log_exception(logger, "internal_error", exc, include_stack=True, error_code="INTERNAL_ERROR", status_code=500)
    return error_response("INTERNAL_ERROR", "An unexpected error occurred", 500)


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if is_database_busy(exc):
        log_exception(logger, "database_busy", exc, include_stack=True, error_code="database_busy", status_code=503)
        return error_response(
            code="database_busy",
            message="Database is temporarily busy; retry the request",
            status_code=503,
        )
    log_exception(logger, "internal_error", exc, include_stack=True, error_code="INTERNAL_ERROR", status_code=500)
    return error_response(
        code="INTERNAL_ERROR",
        message="An unexpected error occurred",
        status_code=500,
    )
