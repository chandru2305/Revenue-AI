"""Append-only audit trail writer.

This is the only code path allowed to insert into `audit_events`. Callers
never mutate or delete audit rows.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ActorType
from app.models.audit_event import AuditEvent
from app.repositories.audit_event_repository import AuditEventRepository


async def record_event(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    event_type: str,
    actor_type: ActorType,
    payload: dict,
    correlation_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        actor_type=actor_type,
        payload=payload,
        correlation_id=correlation_id,
    )
    repo = AuditEventRepository(session)
    return await repo.add(event)
