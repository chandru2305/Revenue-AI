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


def parse_event(payload: dict[str, Any]) -> ParsedWebhookEvent:
    event_type = payload.get("event", "")
    payment_link_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})

    payment_link_id = payment_link_entity.get("id")
    payment_id = payment_entity.get("id")

    return ParsedWebhookEvent(
        event_type=event_type,
        payment_link_id=payment_link_id,
        payment_link_status=payment_link_entity.get("status"),
        amount_paid=payment_link_entity.get("amount_paid"),
        payment_id=payment_id,
        dedup_key=f"{event_type}:{payment_link_id or ''}:{payment_id or ''}",
    )
