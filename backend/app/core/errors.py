"""Central exception types and FastAPI exception handlers.

Error responses are intentionally minimal in production: no stack traces,
no internal details, just a stable error code, a human-readable message, and
the correlation ID so the incident can be traced server-side.
"""
from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_correlation_id, get_logger

logger = get_logger(__name__)


class RecoverAIError(Exception):
    """Base class for domain/application errors with a stable error code."""

    error_code = "internal_error"
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(RecoverAIError):
    error_code = "not_found"
    http_status = status.HTTP_404_NOT_FOUND


class InvalidStateTransitionError(RecoverAIError):
    error_code = "invalid_state_transition"
    http_status = status.HTTP_409_CONFLICT


class ConcurrentModificationError(RecoverAIError):
    """Raised when an optimistic-locking check
    (`RecoveryCase.__mapper_args__["version_id_col"]`) detects that a case
    was modified by another request between when this request read it and
    when it tried to write — e.g. two simultaneous diagnose/execute calls
    on the same case. See docs/ai-architecture.md and
    docs/razorpay-integration.md "Concurrency"."""

    error_code = "concurrent_modification"
    http_status = status.HTTP_409_CONFLICT


class ValidationFailedError(RecoverAIError):
    error_code = "validation_failed"
    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY


def _error_body(error_code: str, message: str) -> dict:
    body = {"error_code": error_code, "message": message}
    correlation_id = get_correlation_id()
    if correlation_id:
        body["correlation_id"] = correlation_id
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RecoverAIError)
    async def handle_recoverai_error(request: Request, exc: RecoverAIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.error_code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_body("validation_failed", "Request validation failed."),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception while processing request")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("internal_error", "An unexpected error occurred."),
        )
