from __future__ import annotations

from sqlalchemy import func, select

from app.domain.enums import PaymentStatus
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.repositories.base import BaseRepository


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    async def list_failed_without_recovery_case(self, limit: int) -> list[Payment]:
        """Failed payments that have no `RecoveryCase` yet — the input to a
        discovery sweep. Oldest first, so the longest-leaking revenue is
        picked up before newer failures."""
        stmt = (
            select(Payment)
            .outerjoin(RecoveryCase, RecoveryCase.payment_id == Payment.id)
            .where(Payment.status == PaymentStatus.FAILED, RecoveryCase.id.is_(None))
            .order_by(Payment.created_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_failed_with_recovery_case(self) -> int:
        """Failed payments that already have a case — reported by a
        discovery sweep as `skipped_existing` so the operator can see how
        much of the failed-payment backlog is already tracked."""
        stmt = (
            select(func.count())
            .select_from(Payment)
            .join(RecoveryCase, RecoveryCase.payment_id == Payment.id)
            .where(Payment.status == PaymentStatus.FAILED)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_paginated(
        self, offset: int, limit: int, status: PaymentStatus | None = None
    ) -> tuple[list[Payment], int]:
        stmt = select(Payment).order_by(Payment.created_at.desc())
        count_stmt = select(func.count()).select_from(Payment)
        if status is not None:
            stmt = stmt.where(Payment.status == status)
            count_stmt = count_stmt.where(Payment.status == status)

        total = (await self.session.execute(count_stmt)).scalar_one()
        rows = (await self.session.execute(stmt.offset(offset).limit(limit))).scalars().all()
        return list(rows), total
