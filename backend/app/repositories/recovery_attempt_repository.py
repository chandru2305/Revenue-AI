from __future__ import annotations

from sqlalchemy import select

from app.models.recovery_attempt import RecoveryAttempt
from app.repositories.base import BaseRepository


class RecoveryAttemptRepository(BaseRepository[RecoveryAttempt]):
    model = RecoveryAttempt

    async def get_by_idempotency_key(self, idempotency_key: str) -> RecoveryAttempt | None:
        stmt = select(RecoveryAttempt).where(RecoveryAttempt.idempotency_key == idempotency_key)
        return (await self.session.execute(stmt)).scalar_one_or_none()
