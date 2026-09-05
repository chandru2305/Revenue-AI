"""RecoveryAttempt model — one bounded execution of a recovery action."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.domain.enums import RecoveryAction, RecoveryAttemptStatus

if TYPE_CHECKING:
    from app.models.recovery_case import RecoveryCase
    from app.models.recovery_payment_request import RecoveryPaymentRequest


class RecoveryAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "recovery_attempts"

    recovery_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recovery_cases.id"), nullable=False, index=True
    )

    action: Mapped[RecoveryAction] = mapped_column(String(32), nullable=False)
    status: Mapped[RecoveryAttemptStatus] = mapped_column(
        String(32), default=RecoveryAttemptStatus.PENDING, nullable=False
    )

    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Set once, at creation, before the provider call is made — this is
    # what a caller checks to detect "have I already tried this?" without
    # relying on provider-side idempotency (which Payment Links don't
    # document). Unique so a bug can never silently create two attempts
    # with the same key.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    # 128, matching AuditEvent.correlation_id — see that column's comment.
    # An orchestrator-driven execution passes the same
    # "<cycle_correlation_id>:<case_id>" value in here.
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    recovery_case: Mapped[RecoveryCase] = relationship(back_populates="attempts")
    payment_request: Mapped[RecoveryPaymentRequest | None] = relationship(
        back_populates="recovery_attempt", uselist=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RecoveryAttempt id={self.id} action={self.action} status={self.status}>"
