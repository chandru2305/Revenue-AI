from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.dependencies import get_ai_service
from app.ai.service import AIRecommendationService
from app.core.logging import get_correlation_id
from app.db.session import get_db
from app.domain.enums import RecoveryCaseStatus
from app.domain.providers.base import PaymentProvider
from app.payments.dependencies import get_payment_provider
from app.schemas.common import PaginatedResponse
from app.schemas.diagnosis import DiagnosisResponse
from app.schemas.execution import ExecutionResponse
from app.schemas.ingestion import (
    DiscoveryReport,
    RecoveryCaseCreatedResponse,
    RecoveryCaseCreateRequest,
)
from app.schemas.recovery_case import RecoveryCaseDetail, RecoveryCaseRead
from app.schemas.timeline import TimelineResponse
from app.services import (
    diagnosis_service,
    execution_service,
    ingestion_service,
    recovery_case_service,
    timeline_service,
)

router = APIRouter(prefix="/recovery-cases", tags=["recovery-cases"])


@router.get("", response_model=PaginatedResponse[RecoveryCaseRead])
async def list_recovery_cases(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: RecoveryCaseStatus | None = None,
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[RecoveryCaseRead]:
    offset = (page - 1) * page_size
    rows, total = await recovery_case_service.list_recovery_cases(
        session, offset=offset, limit=page_size, status=status
    )
    return PaginatedResponse[RecoveryCaseRead](
        items=[RecoveryCaseRead.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", response_model=RecoveryCaseCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_recovery_case(
    request: RecoveryCaseCreateRequest, session: AsyncSession = Depends(get_db)
) -> RecoveryCaseCreatedResponse:
    """Open a recovery case for one existing failed payment. Idempotent —
    a second call for the same payment returns the existing case with
    `created: false`. 404 if the payment doesn't exist, 422 if it isn't
    in a failed state.
    """
    correlation_id = get_correlation_id() or str(uuid.uuid4())
    return await ingestion_service.create_recovery_case_for_payment(
        session, request.payment_id, correlation_id=correlation_id
    )


@router.post("/discover", response_model=DiscoveryReport)
async def discover_recovery_cases(
    limit: int = Query(default=100, ge=1, le=500), session: AsyncSession = Depends(get_db)
) -> DiscoveryReport:
    """Sweep for failed payments that have no recovery case yet and open
    one for each. Safe to run repeatedly (e.g. on a schedule) — only
    un-cased failed payments are ever picked up.
    """
    correlation_id = get_correlation_id() or str(uuid.uuid4())
    return await ingestion_service.discover_failed_payments(
        session, correlation_id=correlation_id, limit=limit
    )


@router.get("/{case_id}", response_model=RecoveryCaseDetail)
async def get_recovery_case(
    case_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> RecoveryCaseDetail:
    case = await recovery_case_service.get_recovery_case(session, case_id)
    return RecoveryCaseDetail.model_validate(case)


@router.post("/{case_id}/diagnose", response_model=DiagnosisResponse)
async def diagnose_recovery_case(
    case_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    ai_service: AIRecommendationService = Depends(get_ai_service),
) -> DiagnosisResponse:
    """Runs AI diagnosis + recommendation for one recovery case, then
    evaluates the result against the deterministic policy engine.

    Does NOT execute any recovery action — this only ever moves the case
    to APPROVED (ready for a future execution phase), STOPPED, or
    ESCALATED. See docs/ai-safety.md.
    """
    correlation_id = get_correlation_id() or str(uuid.uuid4())
    return await diagnosis_service.diagnose_recovery_case(
        session, case_id, ai_service=ai_service, correlation_id=correlation_id
    )


@router.post("/{case_id}/execute", response_model=ExecutionResponse)
async def execute_recovery_case(
    case_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> ExecutionResponse:
    """Executes the approved recovery action against Razorpay Test Mode
    (currently: SEND_PAYMENT_LINK only). Only an APPROVED case can execute
    — this call takes no body; amount and action are always read from the
    case's own canonical record, never from the request. Re-checks policy
    with fresh data before doing anything. Never returns RECOVERED itself
    — that only happens once a webhook confirms the payment. See
    docs/razorpay-integration.md.
    """
    correlation_id = get_correlation_id() or str(uuid.uuid4())
    return await execution_service.execute_recovery_case(
        session, case_id, provider=provider, correlation_id=correlation_id
    )


@router.get("/{case_id}/timeline", response_model=TimelineResponse)
async def get_recovery_case_timeline(
    case_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> TimelineResponse:
    return await timeline_service.get_case_timeline(session, case_id)
