"""Failed-payment ingestion — the entry point of the recovery workflow.

Turns a provider-reported failed payment into a `Payment` row and a
`RecoveryCase` in `DISCOVERED`, which the existing diagnosis pipeline
(`diagnosis_service`) then picks up. This module adds no new state
transitions and no policy logic: a freshly created case starts at
`DISCOVERED` exactly as the state machine already defines, and its
`revenue_at_risk` is copied verbatim from the canonical `Payment.amount`.

Idempotency:
- `create_recovery_case_for_payment` is safe to call repeatedly for the
  same payment — the `RecoveryCase.payment_id` unique constraint means at
  most one case ever exists per payment; a second call returns the
  existing one with `created=False`.
- `discover_failed_payments` only ever looks at failed payments that have
  no case yet, so re-running a sweep creates nothing the first sweep
  already created.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailedError
from app.domain.enums import ActorType, PaymentStatus, RecoveryCaseStatus
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.repositories.payment_repository import PaymentRepository
from app.schemas.ingestion import (
    DiscoveryReport,
    PaymentIngestRequest,
    PaymentIngestResponse,
    RecoveryCaseCreatedResponse,
)
from app.services import audit_service


async def _find_or_create_customer(session: AsyncSession, external_reference: str | None) -> Customer:
    if external_reference:
        existing = (
            await session.execute(
                select(Customer).where(Customer.external_reference == external_reference)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    customer = Customer(external_reference=external_reference)
    session.add(customer)
    await session.flush()
    return customer


async def _create_case(
    session: AsyncSession, payment: Payment, *, correlation_id: str
) -> RecoveryCase:
    case = RecoveryCase(
        payment_id=payment.id,
        status=RecoveryCaseStatus.DISCOVERED,
        revenue_at_risk=payment.amount,  # canonical — copied, never recomputed
    )
    session.add(case)
    await session.flush()

    await audit_service.record_event(
        session,
        entity_type="recovery_case",
        entity_id=case.id,
        event_type="recovery_case_created",
        actor_type=ActorType.SYSTEM,
        payload={
            "payment_id": str(payment.id),
            "revenue_at_risk": payment.amount,
            "source": "ingestion",
        },
        correlation_id=correlation_id,
    )
    return case


async def create_recovery_case_for_payment(
    session: AsyncSession, payment_id: uuid.UUID, *, correlation_id: str
) -> RecoveryCaseCreatedResponse:
    """Create the recovery case for one existing failed payment. Idempotent."""
    payment_repo = PaymentRepository(session)
    payment = await payment_repo.get(payment_id)
    if payment is None:
        raise NotFoundError(f"Payment '{payment_id}' was not found.")

    if payment.status != PaymentStatus.FAILED:
        raise ValidationFailedError(
            f"Payment '{payment_id}' is '{PaymentStatus(payment.status).value}', not 'failed' — "
            "only failed payments are eligible for a recovery case."
        )

    existing = (
        await session.execute(select(RecoveryCase).where(RecoveryCase.payment_id == payment_id))
    ).scalar_one_or_none()
    if existing is not None:
        return RecoveryCaseCreatedResponse(
            recovery_case_id=existing.id,
            payment_id=payment_id,
            status=RecoveryCaseStatus(existing.status),
            revenue_at_risk=existing.revenue_at_risk,
            created=False,
            correlation_id=correlation_id,
        )

    case = await _create_case(session, payment, correlation_id=correlation_id)
    await session.commit()

    return RecoveryCaseCreatedResponse(
        recovery_case_id=case.id,
        payment_id=payment_id,
        status=RecoveryCaseStatus(case.status),
        revenue_at_risk=case.revenue_at_risk,
        created=True,
        correlation_id=correlation_id,
    )


async def ingest_payment(
    session: AsyncSession, request: PaymentIngestRequest, *, correlation_id: str
) -> PaymentIngestResponse:
    """Record a provider-reported payment and (for failed payments, when
    `auto_create_case` is set) open its recovery case in one call."""
    customer = await _find_or_create_customer(session, request.customer_reference)

    customer.total_payments_count += 1
    if request.status == PaymentStatus.FAILED:
        customer.total_failed_payments_count += 1

    payment = Payment(
        customer_id=customer.id,
        amount=request.amount,
        currency=request.currency.upper(),
        status=request.status,
        payment_method_type=request.payment_method_type,
        failure_reason=request.failure_reason,
        attempt_number=request.attempt_number,
        provider_payment_id=request.provider_payment_id,
    )
    session.add(payment)
    await session.flush()

    await audit_service.record_event(
        session,
        entity_type="payment",
        entity_id=payment.id,
        event_type="payment_ingested",
        actor_type=ActorType.SYSTEM,
        payload={
            "status": PaymentStatus(payment.status).value,
            "amount": payment.amount,
            "currency": payment.currency,
            "failure_reason": request.failure_reason.value if request.failure_reason else None,
        },
        correlation_id=correlation_id,
    )

    case: RecoveryCase | None = None
    if request.auto_create_case and request.status == PaymentStatus.FAILED:
        case = await _create_case(session, payment, correlation_id=correlation_id)

    await session.commit()

    return PaymentIngestResponse(
        payment_id=payment.id,
        customer_id=customer.id,
        recovery_case_id=case.id if case is not None else None,
        recovery_case_status=RecoveryCaseStatus(case.status) if case is not None else None,
        correlation_id=correlation_id,
    )


async def discover_failed_payments(
    session: AsyncSession, *, correlation_id: str, limit: int = 100
) -> DiscoveryReport:
    """Sweep for failed payments with no recovery case and open one for
    each. Safe to run repeatedly (a cron, a button) — only un-cased
    failures are ever picked up."""
    payment_repo = PaymentRepository(session)
    candidates = await payment_repo.list_failed_without_recovery_case(limit)
    already_tracked = await payment_repo.count_failed_with_recovery_case()

    case_ids: list[uuid.UUID] = []
    for payment in candidates:
        case = await _create_case(session, payment, correlation_id=correlation_id)
        case_ids.append(case.id)

    await session.commit()

    return DiscoveryReport(
        scanned=len(candidates),
        created=len(case_ids),
        skipped_existing=already_tracked,
        case_ids=case_ids,
        generated_at=datetime.now(UTC),
        correlation_id=correlation_id,
    )
