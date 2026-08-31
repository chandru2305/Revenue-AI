"""Payment model — a single payment attempt as reported by the provider."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.domain.enums import FailureReason, PaymentMethodType, PaymentStatus

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.recovery_case import RecoveryCase


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payments"

    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)

    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # smallest currency unit
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)

    status: Mapped[PaymentStatus] = mapped_column(String(32), nullable=False)
    payment_method_type: Mapped[PaymentMethodType] = mapped_column(String(32), nullable=False)
    failure_reason: Mapped[FailureReason | None] = mapped_column(String(32), nullable=True)

    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="payments")
    recovery_case: Mapped[RecoveryCase | None] = relationship(back_populates="payment", uselist=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Payment id={self.id} status={self.status}>"
