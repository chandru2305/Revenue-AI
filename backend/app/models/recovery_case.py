"""RecoveryCase model — a revenue-recovery opportunity for one failed payment."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.domain.enums import DiagnosisCategory, RecoveryAction, RecoveryCaseStatus

if TYPE_CHECKING:
    from app.models.payment import Payment
    from app.models.recovery_attempt import RecoveryAttempt
    from app.models.recovery_payment_request import RecoveryPaymentRequest


class RecoveryCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "recovery_cases"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payments.id"), nullable=False, unique=True, index=True
    )

    status: Mapped[RecoveryCaseStatus] = mapped_column(
        String(32), default=RecoveryCaseStatus.DISCOVERED, nullable=False, index=True
    )

    # Optimistic-concurrency version counter. `__mapper_args__` below adds
    # `AND version = :version` to every UPDATE and SQLAlchemy raises
    # StaleDataError (caught in services and turned into
    # ConcurrentModificationError -> HTTP 409) if zero rows matched — i.e.
    # another request already changed this row. This is what actually
    # fixes the Phase 2-documented concurrency gap: two simultaneous
    # diagnose/execute calls on the same case can no longer both silently
    # "win". See docs/razorpay-integration.md "Concurrency".
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}

    revenue_at_risk: Mapped[int] = mapped_column(Integer, nullable=False)
    recovered_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    diagnosis_category: Mapped[DiagnosisCategory | None] = mapped_column(String(32), nullable=True)
    diagnosis_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recovery_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_action: Mapped[RecoveryAction | None] = mapped_column(String(32), nullable=True)

    current_attempt_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    customer_contact_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    policy_version: Mapped[str | None] = mapped_column(String(16), nullable=True)

    payment: Mapped[Payment] = relationship(back_populates="recovery_case")
    attempts: Mapped[list[RecoveryAttempt]] = relationship(
        back_populates="recovery_case", cascade="all, delete-orphan", order_by="RecoveryAttempt.created_at"
    )
    payment_requests: Mapped[list[RecoveryPaymentRequest]] = relationship(
        back_populates="recovery_case",
        cascade="all, delete-orphan",
        order_by="RecoveryPaymentRequest.created_at",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RecoveryCase id={self.id} status={self.status}>"
