from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.enums import RecoveryCaseStatus
from app.models.recovery_case import RecoveryCase
from app.repositories.recovery_case_repository import RecoveryCaseRepository


async def list_recovery_cases(
    session: AsyncSession, offset: int, limit: int, status: RecoveryCaseStatus | None = None
) -> tuple[list[RecoveryCase], int]:
    repo = RecoveryCaseRepository(session)
    return await repo.list_paginated(offset=offset, limit=limit, status=status)


async def get_recovery_case(session: AsyncSession, case_id: uuid.UUID) -> RecoveryCase:
    repo = RecoveryCaseRepository(session)
    case = await repo.get_with_attempts(case_id)
    if case is None:
        raise NotFoundError(f"Recovery case '{case_id}' was not found.")
    return case
