from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories.audit_event_repository import AuditEventRepository
from app.schemas.audit_event import AuditEventRead
from app.schemas.common import PaginatedResponse
from app.services.audit_export_service import fetch_export_rows, rows_to_csv, rows_to_json

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


@router.get("/export")
async def export_audit_events(
    format: Literal["csv", "json"] = Query(default="csv"),
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    event_type: str | None = None,
    correlation_id: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Download the audit trail — the same append-only rows
    `GET /audit-events` paginates, unpaginated and in true chronological
    order (oldest first). Read-only: this endpoint has no write path and
    cannot alter a record. Filters mirror the list endpoint, so exporting
    "what I'm currently looking at" is a query-string away.

    No credential ever reaches the response — every column beyond the
    event's own identifiers is drawn from `AuditEvent.payload`, whose
    keys are re-checked against the same redaction filter
    `app.core.logging` applies to log lines (see
    `app.services.audit_export_service`).
    """
    rows = await fetch_export_rows(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        correlation_id=correlation_id,
    )

    if format == "json":
        return Response(
            content=rows_to_json(rows),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="audit_trail.json"'},
        )

    return Response(
        content=rows_to_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit_trail.csv"'},
    )
