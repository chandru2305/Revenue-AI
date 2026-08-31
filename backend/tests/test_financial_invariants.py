"""Deterministic financial invariants that must hold no matter what path a
case took to get there. See docs/razorpay-integration.md "Financial
invariants" — section 25 of the Phase 3 brief.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.domain.enums import (
    PaymentMethodType,
    PaymentStatus,
    RecoveryAction,
    RecoveryCaseStatus,
)
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.payments.providers.fake import FakePaymentProvider
from app.services import execution_service


async def _seed_approved_case(db_session, *, amount=15_000):
    customer = Customer()
    db_session.add(customer)
    await db_session.flush()
    payment = Payment(
        customer_id=customer.id,
        amount=amount,
        currency="INR",
        status=PaymentStatus.FAILED,
        payment_method_type=PaymentMethodType.CARD,
        attempt_number=1,
    )
    db_session.add(payment)
    await db_session.flush()
    case = RecoveryCase(
        payment_id=payment.id,
        status=RecoveryCaseStatus.APPROVED,
        revenue_at_risk=amount,
        eligible=True,
        recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
        recovery_confidence=0.9,
        policy_version="v1",
    )
    db_session.add(case)
    await db_session.flush()
    return case


@pytest.mark.asyncio
async def test_recovered_amount_starts_at_zero_and_never_negative(db_session):
    case = await _seed_approved_case(db_session)
    assert case.recovered_amount == 0
    assert case.recovered_amount >= 0


@pytest.mark.asyncio
async def test_executing_a_case_never_sets_recovered_amount(db_session):
    # Creating a payment link is "recovery initiated," not "recovered" —
    # recovered_amount must stay exactly 0 until a webhook confirms
    # payment. This is the single most important financial invariant in
    # the whole system.
    case = await _seed_approved_case(db_session)

    result = await execution_service.execute_recovery_case(
        db_session, case.id, provider=FakePaymentProvider(), correlation_id="test"
    )

    assert result.executed is True
    await db_session.refresh(case)
    assert case.recovered_amount == 0
    assert case.status == RecoveryCaseStatus.EXECUTING


@pytest.mark.asyncio
async def test_amount_sent_to_provider_always_equals_canonical_payment_amount(db_session):
    case = await _seed_approved_case(db_session, amount=249_900)
    provider = FakePaymentProvider()

    await execution_service.execute_recovery_case(
        db_session, case.id, provider=provider, correlation_id="test"
    )

    assert len(provider.created_links) == 1
    assert provider.created_links[0].amount == 249_900 == case.payment.amount


@pytest.mark.asyncio
async def test_revenue_at_risk_is_set_from_the_payment_amount_at_creation(db_session):
    case = await _seed_approved_case(db_session, amount=75_000)
    stmt = select(Payment).where(Payment.id == case.payment_id)
    payment = (await db_session.execute(stmt)).scalar_one()
    assert case.revenue_at_risk == 75_000
    assert case.revenue_at_risk == payment.amount


@pytest.mark.asyncio
async def test_multiple_cases_never_cross_contaminate_recovered_amounts(db_session):
    case_1 = await _seed_approved_case(db_session, amount=10_000)
    case_2 = await _seed_approved_case(db_session, amount=20_000)

    await execution_service.execute_recovery_case(
        db_session, case_1.id, provider=FakePaymentProvider(), correlation_id="c1"
    )
    await execution_service.execute_recovery_case(
        db_session, case_2.id, provider=FakePaymentProvider(), correlation_id="c2"
    )

    stmt = select(RecoveryCase).order_by(RecoveryCase.revenue_at_risk)
    cases = (await db_session.execute(stmt)).scalars().all()
    assert [c.recovered_amount for c in cases] == [0, 0]
    assert [c.revenue_at_risk for c in cases] == [10_000, 20_000]
