from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1 import (
    audit_events,
    demo,
    evaluation,
    orchestrator,
    payments,
    recovery_cases,
    system,
    webhooks,
)
from app.core.auth import require_api_key

# NOTE: /health is intentionally NOT part of this router — it is mounted at
# the application root (no /api/v1 prefix) in app/main.py, since it is an
# infrastructure endpoint (load balancers, uptime checks) rather than a
# versioned API resource. It is also therefore not behind the API key,
# which is correct: a health probe shouldn't need credentials.
api_router = APIRouter()

# Every resource router is behind the API key (a no-op when none is
# configured — see app/core/auth.py).
_authenticated = [Depends(require_api_key)]

api_router.include_router(payments.router, dependencies=_authenticated)
api_router.include_router(recovery_cases.router, dependencies=_authenticated)
api_router.include_router(audit_events.router, dependencies=_authenticated)
api_router.include_router(evaluation.router, dependencies=_authenticated)
api_router.include_router(orchestrator.router, dependencies=_authenticated)
api_router.include_router(demo.router, dependencies=_authenticated)
api_router.include_router(system.router, dependencies=_authenticated)

# DELIBERATELY UNAUTHENTICATED, and this is not an oversight: Razorpay
# calls this endpoint and has no way to send our API key. It authenticates
# every request by HMAC-SHA256 signature over the raw body instead
# (app/payments/webhooks.py), which is a stronger guarantee than a shared
# bearer key — it proves the payload is untampered, not merely that the
# caller knows a secret. An unsigned or wrongly-signed request is rejected
# with 401 before the body is even parsed.
api_router.include_router(webhooks.router)
