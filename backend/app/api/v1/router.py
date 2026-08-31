from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import audit_events, evaluation, payments, recovery_cases, webhooks

# NOTE: /health is intentionally NOT part of this router — it is mounted at
# the application root (no /api/v1 prefix) in app/main.py, since it is an
# infrastructure endpoint (load balancers, uptime checks) rather than a
# versioned API resource.
api_router = APIRouter()
api_router.include_router(payments.router)
api_router.include_router(recovery_cases.router)
api_router.include_router(audit_events.router)
api_router.include_router(evaluation.router)
api_router.include_router(webhooks.router)
