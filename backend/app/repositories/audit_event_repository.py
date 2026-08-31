from __future__ import annotations

import uuid

from sqlalchemy import and_, func, or_, select

from app.models.audit_event import AuditEvent
from app.repositories.base import BaseRepository


class AuditEventRepository(BaseRepository[AuditEvent]):
    model = AuditEvent

    async def list_paginated(
        self,
        offset: int,
        limit: int,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        event_type: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[list[AuditEvent], int]:
        stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc())
        count_stmt = select(func.count()).select_from(AuditEvent)
        if entity_type is not None:
            stmt = stmt.where(AuditEvent.entity_type == entity_type)
            count_stmt = count_stmt.where(AuditEvent.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(AuditEvent.entity_id == entity_id)
            count_stmt = count_stmt.where(AuditEvent.entity_id == entity_id)
        if event_type is not None:
            stmt = stmt.where(AuditEvent.event_type == event_type)
            count_stmt = count_stmt.where(AuditEvent.event_type == event_type)
        if correlation_id is not None:
            stmt = stmt.where(AuditEvent.correlation_id == correlation_id)
            count_stmt = count_stmt.where(AuditEvent.correlation_id == correlation_id)

        total = (await self.session.execute(count_stmt)).scalar_one()
        rows = (await self.session.execute(stmt.offset(offset).limit(limit))).scalars().all()
        return list(rows), total

    async def get_case_timeline(
        self, case_id: uuid.UUID, attempt_ids: list[uuid.UUID]
    ) -> list[AuditEvent]:
        """Every audit event for one recovery case, in chronological
        (oldest-first) order — combining case-level events
        (entity_type="recovery_case") with events recorded against its
        attempts (entity_type="recovery_attempt"), since execution-time
        events (e.g. "provider_ambiguous_result") are recorded against the
        RecoveryAttempt, not the case, to keep the audit model's existing
        (entity_type, entity_id) shape rather than inventing a new one."""
        conditions = [and_(AuditEvent.entity_type == "recovery_case", AuditEvent.entity_id == case_id)]
        if attempt_ids:
            conditions.append(
                and_(AuditEvent.entity_type == "recovery_attempt", AuditEvent.entity_id.in_(attempt_ids))
            )
        stmt = select(AuditEvent).where(or_(*conditions)).order_by(AuditEvent.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())
