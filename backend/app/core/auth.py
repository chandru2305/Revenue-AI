"""API-key authentication for the versioned API.

Deliberately simple: a single shared key, supplied as `X-API-Key`, compared
in constant time. This is a deployment gate — "is this caller allowed to
reach this deployment at all" — not per-user authentication. There are no
users, roles, or sessions in this system, and inventing them would be a
bigger design decision than this codebase currently needs. Anything that
needs to attribute an action to a *person* still records
`ActorType.HUMAN` in the audit trail independently.

Configuration posture, matching the rest of the codebase:

- **Key unset (default)** — auth is not enforced, and startup logs a
  warning. This keeps `make up` and local development zero-config, the
  same way an unset `GROQ_API_KEY` degrades to a safe fallback rather
  than crashing.
- **Key unset AND `APP_ENV=production`** — the application refuses to
  start. Silently serving an unauthenticated API in production is the one
  outcome worth failing loudly over, exactly like the `RAZORPAY_MODE`
  guard in `app/payments/providers/razorpay.py`.

The webhook endpoint is deliberately NOT behind this — Razorpay cannot
send our key. It authenticates by HMAC signature instead, which is
strictly stronger for that path. See `app/api/v1/router.py`.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import Header, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)


class InsecureConfigurationError(RuntimeError):
    """Raised at startup when a production deployment has no API key.

    Never caught and converted into a warning — a production API serving
    unauthenticated traffic must fail to start, not degrade.
    """


def validate_auth_configuration(settings: Settings | None = None) -> None:
    """Called once from `create_app`. Fails closed in production."""
    settings = settings or get_settings()
    if settings.api_key:
        return

    if settings.is_production:
        raise InsecureConfigurationError(
            "API_KEY must be set when APP_ENV=production. Refusing to start an "
            "unauthenticated API in production — see docs/security.md."
        )

    log_event(
        logger,
        logging.WARNING,
        "api_auth_disabled",
        reason="API_KEY is not set",
        app_env=settings.app_env,
        detail="Every API endpoint is reachable without credentials. Set API_KEY to enable auth.",
    )


async def require_api_key(x_api_key: str = Header(default="")) -> None:
    """FastAPI dependency. No-op when no key is configured (see module docstring)."""
    expected = get_settings().api_key
    if not expected:
        return

    if not secrets.compare_digest(x_api_key, expected):
        # Deliberately identical response for "missing" and "wrong" — a
        # caller without the key learns nothing about which it was.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-API-Key header is required.",
            headers={"WWW-Authenticate": "X-API-Key"},
        )
