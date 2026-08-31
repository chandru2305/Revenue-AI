"""Customer model.

Intentionally minimal: we store aggregate behavioral signals needed for
recovery decisions, not personal data. Contact details (email/phone) belong
to the payment provider's customer record, not this table.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.payment import Payment


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customers"

    # Opaque reference into the payment provider's customer record.
    external_reference: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    total_payments_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_failed_payments_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_recovered_amount: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    payments: Mapped[list[Payment]] = relationship(back_populates="customer")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Customer id={self.id}>"
