from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.domain.enums import RecoveryCaseStatus
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.repositories.base import BaseRepository


class RecoveryCaseRepository(BaseRepository[RecoveryCase]):
    model = RecoveryCase

    async def list_paginated(
        self, offset: int, limit: int, status: RecoveryCaseStatus | None = None
    ) -> tuple[list[RecoveryCase], int]:
        stmt = select(RecoveryCase).order_by(RecoveryCase.created_at.desc())
        count_stmt = select(func.count()).select_from(RecoveryCase)
        if status is not None:
            stmt = stmt.where(RecoveryCase.status == status)
            count_stmt = count_stmt.where(RecoveryCase.status == status)

        total = (await self.session.execute(count_stmt)).scalar_one()
        rows = (await self.session.execute(stmt.offset(offset).limit(limit))).scalars().all()
        return list(rows), total

    async def get_with_attempts(self, case_id: uuid.UUID) -> RecoveryCase | None:
        stmt = (
            select(RecoveryCase)
            .where(RecoveryCase.id == case_id)
            .options(
                selectinload(RecoveryCase.attempts),
                selectinload(RecoveryCase.payment),
                selectinload(RecoveryCase.payment_requests),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_for_diagnosis(self, case_id: uuid.UUID) -> RecoveryCase | None:
        """Eager-loads everything `app.services.diagnosis_service` needs in
        one round trip: the case's attempts, its payment, and that
        payment's customer."""
        stmt = (
            select(RecoveryCase)
            .where(RecoveryCase.id == case_id)
            .options(
                selectinload(RecoveryCase.attempts),
                selectinload(RecoveryCase.payment).selectinload(Payment.customer),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_for_execution(self, case_id: uuid.UUID) -> RecoveryCase | None:
        """Eager-loads everything `app.services.execution_service` needs:
        the case's payment, attempts, and existing payment requests (to
        check for an already-active one before creating another)."""
        stmt = (
            select(RecoveryCase)
            .where(RecoveryCase.id == case_id)
            .options(
                selectinload(RecoveryCase.payment),
                selectinload(RecoveryCase.attempts),
                selectinload(RecoveryCase.payment_requests),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
