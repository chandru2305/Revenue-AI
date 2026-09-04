"""Razorpay webhook signature verification and event parsing.

Signature verification: HMAC-SHA256 over the RAW request body, keyed with
the dashboard-configured webhook secret, hex-encoded, compared with
`hmac.compare_digest` (constant-time — avoids a timing side-channel).
Verified against current official Razorpay webhook documentation during
Phase 3 development. The body MUST be the exact raw bytes FastAPI
received — never re-serialized JSON, which can reorder keys or change
whitespace and silently break verification.

Event parsing is pure (no I/O, no DB) — `app.services.webhook_service`
does the DB/idempotency/state-transition work.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any


def verify_signature(raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    if not webhook_secret or not signature:
        return False
    expected = hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@dataclass(frozen=True)
class ParsedWebhookEvent:
    event_type: str
    payment_link_id: str | None
    payment_link_status: str | None
    amount_paid: int | None
    payment_id: str | None
    dedup_key: str
    # True when `dedup_key` came from Razorpay's own delivery id rather
    # than from the payload's content. Recorded so the audit trail shows
    # which idempotency guarantee a given event actually got.
    dedup_key_source: str = "payload"


def build_dedup_key(payload: dict[str, Any], event_id: str | None = None) -> tuple[str, str]:
    """Returns `(dedup_key, source)`.

    Razorpay sends an `X-Razorpay-Event-Id` header that is stable across
    redeliveries of the same event — the canonical idempotency handle, and
    strictly better than deriving one from the payload. It is preferred
    when present.

    The payload-derived fallback, `(event, payment_link_id, payment_id)`,
    still covers deliveries that arrive without the header. It is slightly
    weaker: two genuinely distinct events sharing all three values would
    collide. In practice a payment link reaches a given status once, so
    that collision is the same "already handled" case we want to suppress
    anyway.
    """
    if event_id:
        return f"event_id:{event_id}", "event_id"

    event_type = payload.get("event", "")
    payment_link_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    payment_link_id = payment_link_entity.get("id")
    payment_id = payment_entity.get("id")
    return f"{event_type}:{payment_link_id or ''}:{payment_id or ''}", "payload"


def parse_event(payload: dict[str, Any], event_id: str | None = None) -> ParsedWebhookEvent:
    event_type = payload.get("event", "")
    payment_link_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})

    dedup_key, dedup_key_source = build_dedup_key(payload, event_id)

    return ParsedWebhookEvent(
        event_type=event_type,
        payment_link_id=payment_link_entity.get("id"),
        payment_link_status=payment_link_entity.get("status"),
        amount_paid=payment_link_entity.get("amount_paid"),
        payment_id=payment_entity.get("id"),
        dedup_key=dedup_key,
        dedup_key_source=dedup_key_source,
    )
