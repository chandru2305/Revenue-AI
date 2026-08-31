"""Shared optimistic-concurrency guard.

`RecoveryCase.__mapper_args__["version_id_col"]` (see
`app.models.recovery_case`) makes SQLAlchemy raise `StaleDataError` when a
flush's UPDATE affects zero rows — i.e. another request already changed
the row this request read. Both `diagnosis_service` and `execution_service`
mutate a RecoveryCase across several flushes before committing, so both
wrap their whole operation with this one helper rather than duplicating
the try/except.

This is the actual fix for the concurrency gap Phase 2 documented and left
open: two simultaneous requests against the *same* case can no longer both
proceed past the point where they'd otherwise both "win".
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.errors import ConcurrentModificationError

T = TypeVar("T")


async def guard_against_concurrent_modification(
    session: AsyncSession, operation: Callable[[], Awaitable[T]]
) -> T:
    try:
        return await operation()
    except StaleDataError as exc:
        await session.rollback()
        raise ConcurrentModificationError(
            "This recovery case was modified by another request while this one was in "
            "progress. Reload it and, if still appropriate, retry."
        ) from exc
