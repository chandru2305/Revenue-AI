from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_correlation_id
from app.db.session import get_db
from app.domain.enums import PaymentStatus
from app.repositories.payment_repository import PaymentRepository
from app.schemas.common import PaginatedResponse
from app.schemas.ingestion import PaymentIngestRequest, PaymentIngestResponse
from app.schemas.payment import PaymentRead
from app.services import ingestion_service

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("", response_model=PaginatedResponse[PaymentRead])
async def list_payments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: PaymentStatus | None = None,
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[PaymentRead]:
    repo = PaymentRepository(session)
    offset = (page - 1) * page_size
    rows, total = await repo.list_paginated(offset=offset, limit=page_size, status=status)
    return PaginatedResponse[PaymentRead](
        items=[PaymentRead.model_validate(row) for row in rows], page=page, page_size=page_size, total=total
    )


@router.post("", response_model=PaymentIngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_payment(
    request: PaymentIngestRequest, session: AsyncSession = Depends(get_db)
) -> PaymentIngestResponse:
    """Ingest one provider-reported payment. A FAILED payment (with
    `auto_create_case`, the default) also opens its recovery case in
    `DISCOVERED`, ready for `POST /recovery-cases/{id}/diagnose`. This is
    the workflow's entry point — nothing here executes a recovery action.
    """
    correlation_id = get_correlation_id() or str(uuid.uuid4())
    return await ingestion_service.ingest_payment(session, request, correlation_id=correlation_id)
