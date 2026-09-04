"""FastAPI application factory."""
from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import health
from app.api.v1.router import api_router
from app.core.auth import validate_auth_configuration
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger, log_event, set_correlation_id
from app.payments.dependencies import close_payment_provider
from app.services import orchestrator_runner

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger("app")

# An inbound correlation ID is caller-supplied and ends up in every log
# line and echoed back in the response, so it is constrained to a safe
# shape rather than trusted: printable ASCII identifiers only, bounded
# length. Anything else is replaced with a generated ID rather than
# rejected — a malformed trace header shouldn't fail an otherwise valid
# request.
_CORRELATION_ID_MAX_LENGTH = 128
_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")


def _safe_correlation_id(raw: str | None) -> str:
    if raw and len(raw) <= _CORRELATION_ID_MAX_LENGTH and _CORRELATION_ID_PATTERN.match(raw):
        return raw
    return str(uuid.uuid4())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log_event(logger, logging.INFO, "application_startup", app_env=settings.app_env)
    # No-op unless ORCHESTRATOR_ENABLED — the autonomous loop never starts
    # itself in dev, tests, or a default `make up`.
    orchestrator_runner.start()
    try:
        yield
    finally:
        await orchestrator_runner.stop()
        # The Razorpay provider holds a pooled httpx.AsyncClient; without
        # this the connections are dropped rather than closed on shutdown.
        await close_payment_provider()
        log_event(logger, logging.INFO, "application_shutdown")


def create_app() -> FastAPI:
    # Fails closed if this is a production deployment with no API key —
    # raises rather than logs, so the process never starts serving an
    # unauthenticated API. See app/core/auth.py.
    validate_auth_configuration(settings)

    app = FastAPI(
        title=settings.app_name,
        description="Bounded, auditable AI revenue-recovery system.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Correlation-Id", "X-API-Key"],
        expose_headers=["X-Correlation-Id"],
    )

    @app.middleware("http")
    async def correlation_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = _safe_correlation_id(request.headers.get("x-correlation-id"))
        set_correlation_id(correlation_id)
        response = await call_next(request)
        response.headers["x-correlation-id"] = correlation_id
        return response

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
