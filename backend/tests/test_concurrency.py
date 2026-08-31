"""Verifies the Phase 2 concurrency gap is actually fixed: two simultaneous
requests against the *same* recovery case can no longer both proceed.

Uses two independent sessions against a shared file-based SQLite database
(not the shared in-memory `db_session` fixture, which — by design, for
test determinism — hands every request the same session object and so
can't model two truly independent app instances/requests).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.providers.fake import FakeAIProvider
from app.ai.schemas import RecoveryRecommendation
from app.ai.service import AIRecommendationService
from app.core.errors import ConcurrentModificationError
from app.db.base import Base
from app.domain.enums import (
    DiagnosisCategory,
    PaymentMethodType,
    PaymentStatus,
    RecoveryAction,
    RecoveryCaseStatus,
)
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.payments.providers.fake import FakePaymentProvider
from app.services import diagnosis_service, execution_service


@pytest.fixture
async def two_sessions(tmp_path):
    db_path = tmp_path / "concurrency_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session_a, session_factory() as session_b:
        yield session_a, session_b

    await engine.dispose()


async def _seed_discovered_case(session: AsyncSession, *, amount=15_000) -> uuid.UUID:
    customer = Customer(total_payments_count=5, total_failed_payments_count=1)
    session.add(customer)
    await session.flush()

    payment = Payment(
        customer_id=customer.id,
        amount=amount,
        currency="INR",
        status=PaymentStatus.FAILED,
        payment_method_type=PaymentMethodType.CARD,
        attempt_number=1,
    )
    session.add(payment)
    await session.flush()

    case = RecoveryCase(payment_id=payment.id, status=RecoveryCaseStatus.DISCOVERED, revenue_at_risk=amount)
    session.add(case)
    await session.commit()
    return case.id


@pytest.mark.asyncio
async def test_simultaneous_diagnose_calls_on_same_case_do_not_both_succeed(two_sessions):
    session_a, session_b = two_sessions
    case_id = await _seed_discovered_case(session_a)

    # Both sessions load the case at version 1, independently, before
    # either has written anything back — the actual race scenario.
    case_a = await session_a.get(RecoveryCase, case_id)
    case_b = await session_b.get(RecoveryCase, case_id)
    assert case_a.version == case_b.version == 1

    recommendation = RecoveryRecommendation(
        diagnosis_category=DiagnosisCategory.TEMPORARY_FAILURE,
        recovery_confidence=0.9,
        recommended_action=RecoveryAction.RETRY_PAYMENT,
        decision_explanation="test",
    )
    ai_service = AIRecommendationService(
        FakeAIProvider(recommendation=recommendation), model_name="fake", max_retries=0
    )

    # session_a runs the whole diagnose flow to completion first.
    await diagnosis_service.diagnose_recovery_case(
        session_a, case_id, ai_service=ai_service, correlation_id="race-a"
    )

    # session_b still holds its stale (pre-session_a-commit) view of the
    # case in its identity map, from the `session_b.get()` above — this is
    # exactly the race: it read the case before session_a's change landed.
    with pytest.raises(ConcurrentModificationError):
        await diagnosis_service.diagnose_recovery_case(
            session_b, case_id, ai_service=ai_service, correlation_id="race-b"
        )


@pytest.mark.asyncio
async def test_simultaneous_execute_calls_on_same_case_do_not_both_succeed(two_sessions):
    session_a, session_b = two_sessions
    customer = Customer()
    session_a.add(customer)
    await session_a.flush()
    payment = Payment(
        customer_id=customer.id,
        amount=15_000,
        currency="INR",
        status=PaymentStatus.FAILED,
        payment_method_type=PaymentMethodType.CARD,
        attempt_number=1,
    )
    session_a.add(payment)
    await session_a.flush()
    case = RecoveryCase(
        payment_id=payment.id,
        status=RecoveryCaseStatus.APPROVED,
        revenue_at_risk=15_000,
        eligible=True,
        recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
        recovery_confidence=0.9,
        policy_version="v1",
    )
    session_a.add(case)
    await session_a.commit()
    case_id = case.id

    # Both sessions read the APPROVED case before either writes anything.
    case_a = await session_a.get(RecoveryCase, case_id)
    case_b = await session_b.get(RecoveryCase, case_id)
    assert case_a.version == case_b.version

    await execution_service.execute_recovery_case(
        session_a, case_id, provider=FakePaymentProvider(), correlation_id="race-a"
    )

    with pytest.raises(ConcurrentModificationError):
        await execution_service.execute_recovery_case(
            session_b, case_id, provider=FakePaymentProvider(), correlation_id="race-b"
        )
