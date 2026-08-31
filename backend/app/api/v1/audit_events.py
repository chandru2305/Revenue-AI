from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories.audit_event_repository import AuditEventRepository
from app.schemas.audit_event import AuditEventRead
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/audit-events", tags=["audit-events"])


@router.get("", response_model=PaginatedResponse[AuditEventRead])
async def list_audit_events(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    event_type: str | None = None,
    correlation_id: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AuditEventRead]:
    repo = AuditEventRepository(session)
    offset = (page - 1) * page_size
    rows, total = await repo.list_paginated(
        offset=offset,
        limit=page_size,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        correlation_id=correlation_id,
    )
    return PaginatedResponse[AuditEventRead](
        items=[AuditEventRead.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )
