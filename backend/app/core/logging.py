"""Structured (JSON) logging with correlation-ID propagation.

Every log line emitted through this module is a single JSON object so logs
are machine-parseable from day one. Correlation IDs are carried via a
ContextVar so any code on the request path can log without threading the ID
through every function signature.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Fragments that must never be logged, even if accidentally present in
# `extra`. Substring match (not exact), so any field containing one of
# these — "webhook_secret", "razorpay_key_secret", "client_token", a
# future "authorization_header", etc. — is caught by construction rather
# than requiring this list to enumerate every possible field name ahead
# of time.
_REDACTED_KEY_FRAGMENTS = ("password", "secret", "token", "api_key", "authorization")


def _is_redacted_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _REDACTED_KEY_FRAGMENTS)


def set_correlation_id(correlation_id: str) -> None:
    _correlation_id_ctx.set(correlation_id)


def get_correlation_id() -> str | None:
    return _correlation_id_ctx.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id

        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                if _is_redacted_key(key):
                    continue
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

    # Quiet noisy third-party loggers unless we're debugging.
    for noisy in ("uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel("WARNING" if level != "DEBUG" else "INFO")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Emit a structured log line with arbitrary key/value fields."""
    logger.log(level, message, extra={"extra_fields": fields})
