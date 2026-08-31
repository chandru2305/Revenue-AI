"""RecoveryPaymentRequest — the provider-side recovery resource (a Razorpay
Payment Link) tied to one RecoveryAttempt.

Unlike AuditEvent, this table IS updated in place: it's a materialized
view of the provider's current state (created -> partially_paid/paid/
expired/cancelled), kept in sync by webhook events
(`app.services.webhook_service`). The immutable history of *how* it got
there lives in AuditEvent, not here.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.domain.enums import RecoveryPaymentRequestStatus

if TYPE_CHECKING:
    from app.models.recovery_attempt import RecoveryAttempt
    from app.models.recovery_case import RecoveryCase


class RecoveryPaymentRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "recovery_payment_requests"

    recovery_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recovery_cases.id"), nullable=False, index=True
    )
    recovery_attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recovery_attempts.id"), nullable=False, unique=True, index=True
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    reference_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    short_url: Mapped[str | None] = mapped_column(String(255), nullable=True)

    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_paid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[RecoveryPaymentRequestStatus] = mapped_column(
        String(32), default=RecoveryPaymentRequestStatus.CREATED, nullable=False, index=True
    )

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    recovery_case: Mapped[RecoveryCase] = relationship(back_populates="payment_requests")
    recovery_attempt: Mapped[RecoveryAttempt] = relationship(back_populates="payment_request")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RecoveryPaymentRequest id={self.id} status={self.status}>"
