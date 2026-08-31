"""Webhook idempotency ledger.

A unique constraint on `dedup_key` is the entire mechanism: processing a
webhook attempts an INSERT first; a constraint violation means this exact
event was already handled, so the handler acks (HTTP 200) without
reprocessing rather than erroring. See `app.services.webhook_service` and
docs/razorpay-integration.md "Webhook idempotency".

Razorpay's webhook payloads don't document a single top-level delivery ID,
so `dedup_key` is derived from `(event, payment_link_id, payment_id)` —
the natural identity of "this payment reached this status on this link."
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
