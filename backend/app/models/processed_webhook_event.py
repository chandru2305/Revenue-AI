"""Webhook idempotency ledger.

A unique constraint on `dedup_key` is the entire mechanism: processing a
webhook attempts an INSERT *before* any state change; a constraint
violation means this exact event was already handled, so the handler acks
(HTTP 200) without reprocessing rather than erroring. That ordering is
load-bearing — see `app.services.webhook_service._claim_event` — because
claiming last would let a lost race roll back an already-applied
transition. Also see docs/razorpay-integration.md "Webhook idempotency".

`dedup_key` prefers Razorpay's `X-Razorpay-Event-Id` delivery header,
which is stable across redeliveries of the same event and is therefore the
canonical idempotency handle. When a delivery arrives without it, the key
falls back to `(event, payment_link_id, payment_id)` — the natural
identity of "this payment reached this status on this link." See
`app.payments.webhooks.build_dedup_key`.
"""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class ProcessedWebhookEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "processed_webhook_events"

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProcessedWebhookEvent id={self.id} event_type={self.event_type}>"
