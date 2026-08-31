"""GET /api/v1/recovery-cases/{id}/timeline — the case's full audit
history in chronological order, straight from AuditEvent rows. Nothing
here is synthesized; every field the frontend timeline shows came from a
real recorded event.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.recovery_case_repository import RecoveryCaseRepository
from app.schemas.timeline import TimelineEvent, TimelineResponse


async def get_case_timeline(session: AsyncSession, case_id: uuid.UUID) -> TimelineResponse:
    case_repo = RecoveryCaseRepository(session)
    case = await case_repo.get_with_attempts(case_id)
    if case is None:
        raise NotFoundError(f"Recovery case '{case_id}' was not found.")

    attempt_ids = [attempt.id for attempt in case.attempts]
    audit_repo = AuditEventRepository(session)
    events = await audit_repo.get_case_timeline(case_id, attempt_ids)

    return TimelineResponse(
        recovery_case_id=case_id, events=[TimelineEvent.model_validate(event) for event in events]
    )
