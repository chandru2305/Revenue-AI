from __future__ import annotations

import uuid

from sqlalchemy import select

from app.domain.enums import RecoveryPaymentRequestStatus
from app.models.recovery_payment_request import RecoveryPaymentRequest
from app.repositories.base import BaseRepository

# Statuses where the link is still "live" — a customer could still pay it,
# so a new one must not be created for the same case while one of these
# exists. See app.services.execution_service.
ACTIVE_STATUSES = frozenset(
    {RecoveryPaymentRequestStatus.CREATED, RecoveryPaymentRequestStatus.PARTIALLY_PAID}
)


class RecoveryPaymentRequestRepository(BaseRepository[RecoveryPaymentRequest]):
    model = RecoveryPaymentRequest

    async def get_by_provider_reference(self, provider_reference: str) -> RecoveryPaymentRequest | None:
        stmt = select(RecoveryPaymentRequest).where(
            RecoveryPaymentRequest.provider_reference == provider_reference
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_reference_id(self, reference_id: str) -> RecoveryPaymentRequest | None:
        stmt = select(RecoveryPaymentRequest).where(RecoveryPaymentRequest.reference_id == reference_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_active_for_case(self, recovery_case_id: uuid.UUID) -> RecoveryPaymentRequest | None:
        stmt = select(RecoveryPaymentRequest).where(
            RecoveryPaymentRequest.recovery_case_id == recovery_case_id,
            RecoveryPaymentRequest.status.in_([s.value for s in ACTIVE_STATUSES]),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
