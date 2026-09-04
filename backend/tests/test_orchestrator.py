"""The autonomous recovery loop.

The behaviours that matter here are the *bounds*, not the happy path: an
unattended loop that moves money must not execute unless explicitly told
to, must respect per-cycle budgets, and must survive one bad case without
aborting the whole pass.
"""
from __future__ import annotations

import uuid

import pytest

from app.ai.providers.fake import FakeAIProvider
from app.ai.schemas import RecoveryRecommendation
from app.ai.service import AIRecommendationService
from app.domain.enums import (
    DiagnosisCategory,
    PaymentMethodType,
    PaymentStatus,
    RecoveryAction,
    RecoveryCaseStatus,
)
from app.models.audit_event import AuditEvent
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.payments.providers.fake import FakePaymentProvider
from app.services import orchestrator_service

APPROVABLE = RecoveryRecommendation(
    diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
    recovery_confidence=0.9,
    recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
    decision_explanation="Insufficient funds on attempt 1; a payment link lets them retry.",
)

STOPPABLE = RecoveryRecommendation(
    diagnosis_category=DiagnosisCategory.REPEATED_FAILURE,
    recovery_confidence=0.1,
    recommended_action=RecoveryAction.STOP,
    decision_explanation="Repeated failures; not worth pursuing.",
)


def _ai(recommendation: RecoveryRecommendation) -> AIRecommendationService:
    return AIRecommendationService(
        FakeAIProvider(recommendation=recommendation), model_name="fake", max_retries=0
    )


async def _seed_failed_payment_with_case(db_session, *, amount=15_000) -> RecoveryCase:
    customer = Customer(total_payments_count=5, total_failed_payments_count=1)
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
        payment_id=payment.id, status=RecoveryCaseStatus.DISCOVERED, revenue_at_risk=amount
    )
    db_session.add(case)
    await db_session.flush()
    return case


async def _seed_uncased_failed_payment(db_session, *, amount=9_000) -> Payment:
    """A failed payment with NO recovery case — what discovery picks up."""
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
    return payment


async def _run(db_session, *, recommendation=APPROVABLE, auto_execute=False, **budgets):
    defaults = {"max_discover": 100, "max_diagnose": 25, "max_execute": 10}
    defaults.update(budgets)
    return await orchestrator_service.run_recovery_cycle(
        db_session,
        ai_service=_ai(recommendation),
        provider=FakePaymentProvider(),
        correlation_id=f"test-cycle-{uuid.uuid4().hex[:8]}",
        auto_execute=auto_execute,
        **defaults,
    )


# --- the loop actually closes ---


@pytest.mark.asyncio
async def test_cycle_discovers_uncased_failed_payments(db_session):
    await _seed_uncased_failed_payment(db_session)
    await db_session.commit()

    report = await _run(db_session)

    assert report.cases_discovered == 1
    # ...and immediately diagnoses what it just discovered, in the same pass
    assert report.cases_diagnosed == 1


@pytest.mark.asyncio
async def test_cycle_diagnoses_and_reaches_approved(db_session):
    await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    report = await _run(db_session)

    assert report.cases_diagnosed == 1
    assert report.approved == 1
    assert report.cases_executed == 0


@pytest.mark.asyncio
async def test_cycle_respects_a_policy_stop(db_session):
    await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    report = await _run(db_session, recommendation=STOPPABLE)

    assert report.stopped == 1
    assert report.approved == 0
    assert report.cases_executed == 0


# --- the safety bounds, which are the point ---


@pytest.mark.asyncio
async def test_auto_execute_off_leaves_approved_cases_for_a_human(db_session):
    """The default posture: the loop reasons and decides, but does not move
    money on its own."""
    await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    report = await _run(db_session, auto_execute=False)

    assert report.approved == 1
    assert report.cases_executed == 0
    (outcome,) = [o for o in report.outcomes if o.final_status == RecoveryCaseStatus.APPROVED]
    # and it says *why* it withheld, rather than silently skipping
    assert "auto_execute is disabled" in (outcome.withheld_reason or "")


@pytest.mark.asyncio
async def test_auto_execute_on_executes_approved_cases(db_session):
    await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    report = await _run(db_session, auto_execute=True)

    assert report.cases_executed == 1
    assert report.auto_execute is True


@pytest.mark.asyncio
async def test_per_cycle_execution_budget_is_enforced(db_session):
    for _ in range(3):
        await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    report = await _run(db_session, auto_execute=True, max_execute=1)

    assert report.cases_diagnosed == 3
    assert report.cases_executed == 1  # not 3
    withheld = [o.withheld_reason for o in report.outcomes if o.withheld_reason]
    assert any("budget exhausted" in (r or "") for r in withheld)


@pytest.mark.asyncio
async def test_per_cycle_diagnosis_budget_is_enforced(db_session):
    for _ in range(4):
        await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    report = await _run(db_session, max_diagnose=2)

    assert report.cases_diagnosed == 2  # not 4


@pytest.mark.asyncio
async def test_a_later_cycle_drains_the_approved_backlog(db_session):
    """Regression, found by a live run: a case left APPROVED by an earlier
    cycle must still be executable later. APPROVED is not a diagnosable
    status, so without an explicit backlog pass the loop could only ever
    execute what it diagnosed in the same cycle — turning auto-execute on
    retroactively would do nothing."""
    await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    first = await _run(db_session, auto_execute=False)
    assert first.approved == 1
    assert first.cases_executed == 0

    # A second cycle diagnoses nothing new, but must pick up the backlog.
    second = await _run(db_session, auto_execute=True)

    assert second.cases_diagnosed == 0
    assert second.cases_executed == 1
    (outcome,) = second.outcomes
    assert outcome.diagnosed is False  # an earlier cycle diagnosed it
    assert outcome.executed is True


@pytest.mark.asyncio
async def test_backlog_drain_respects_the_execution_budget(db_session):
    for _ in range(3):
        await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    await _run(db_session, auto_execute=False)  # 3 approved, none executed
    second = await _run(db_session, auto_execute=True, max_execute=2)

    assert second.cases_executed == 2  # not 3


@pytest.mark.asyncio
async def test_backlog_drain_does_not_double_handle_a_case_from_this_cycle(db_session):
    """A case diagnosed AND executed in the same pass must appear once."""
    await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    report = await _run(db_session, auto_execute=True)

    assert report.cases_executed == 1
    ids = [o.recovery_case_id for o in report.outcomes]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_zero_execution_budget_never_executes_even_when_auto_execute_is_on(db_session):
    await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    report = await _run(db_session, auto_execute=True, max_execute=0)

    assert report.cases_executed == 0
    assert report.approved == 1


# --- resilience ---


@pytest.mark.asyncio
async def test_an_empty_database_produces_a_clean_no_op_cycle(db_session):
    report = await _run(db_session)

    assert report.cases_discovered == 0
    assert report.cases_diagnosed == 0
    assert report.cases_executed == 0
    assert report.cases_failed == 0
    assert report.outcomes == []


@pytest.mark.asyncio
async def test_terminal_cases_are_not_re_processed(db_session):
    """A cycle must be safe to run repeatedly — the second pass should find
    nothing left to do."""
    await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    first = await _run(db_session, recommendation=STOPPABLE)
    second = await _run(db_session, recommendation=STOPPABLE)

    assert first.cases_diagnosed == 1
    assert second.cases_diagnosed == 0  # STOPPED is terminal
    assert second.cases_failed == 0


# --- audit trail ---


@pytest.mark.asyncio
async def test_the_cycle_itself_is_recorded_in_the_audit_trail(db_session):
    """An operator reading the trail must be able to see that a machine,
    not a person, moved these cases."""
    from sqlalchemy import select

    await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    report = await _run(db_session)

    rows = (
        await db_session.execute(
            select(AuditEvent).where(AuditEvent.entity_type == "recovery_cycle")
        )
    ).scalars().all()
    assert len(rows) == 1
    event = rows[0]
    assert event.entity_id == report.cycle_id
    assert event.event_type == "recovery_cycle_completed"
    assert event.payload["auto_execute"] is False
    assert event.payload["cases_diagnosed"] == 1


@pytest.mark.asyncio
async def test_each_case_keeps_its_own_traceable_correlation_id(db_session):
    """Per-case correlation ids are derived from the cycle's, so one case's
    full story is greppable without losing the cycle it belonged to."""
    from sqlalchemy import select

    case = await _seed_failed_payment_with_case(db_session)
    await db_session.commit()

    report = await _run(db_session)

    rows = (
        await db_session.execute(select(AuditEvent).where(AuditEvent.entity_id == case.id))
    ).scalars().all()
    assert rows
    assert all(r.correlation_id.startswith(report.correlation_id) for r in rows)
    assert all(str(case.id) in r.correlation_id for r in rows)
