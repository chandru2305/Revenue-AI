"""End-to-end tests for POST /api/v1/recovery-cases/{id}/diagnose.

Exercises the full orchestration (app.services.diagnosis_service) through
the real HTTP route, with a FakeAIProvider swapped in via FastAPI's
dependency-override mechanism — no network calls, fully deterministic.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.ai.dependencies import get_ai_service
from app.ai.providers.base import AIProviderTimeoutError, AIProviderUnavailableError
from app.ai.providers.fake import FakeAIProvider
from app.ai.schemas import RecoveryRecommendation
from app.ai.service import AIRecommendationService
from app.domain.enums import (
    ActorType,
    DecisionSource,
    DiagnosisCategory,
    PaymentMethodType,
    PaymentStatus,
    PolicyDecisionType,
    RecoveryAction,
    RecoveryCaseStatus,
)
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase


async def _seed_case(
    db_session, *, payment_status=PaymentStatus.FAILED, amount=15_000, current_attempt_number=0
):
    customer = Customer(total_payments_count=5, total_failed_payments_count=1)
    db_session.add(customer)
    await db_session.flush()

    payment = Payment(
        customer_id=customer.id,
        amount=amount,
        currency="INR",
        status=payment_status,
        payment_method_type=PaymentMethodType.CARD,
        attempt_number=1,
    )
    db_session.add(payment)
    await db_session.flush()

    case = RecoveryCase(
        payment_id=payment.id,
        status=RecoveryCaseStatus.DISCOVERED,
        revenue_at_risk=amount,
        current_attempt_number=current_attempt_number,
    )
    db_session.add(case)
    await db_session.flush()
    return case


def _override_ai_service(provider: FakeAIProvider) -> None:
    app.dependency_overrides[get_ai_service] = lambda: AIRecommendationService(
        provider, model_name="fake-model", max_retries=0
    )


async def _audit_event_types(db_session, entity_id: uuid.UUID) -> list[str]:
    stmt = select(AuditEvent).where(AuditEvent.entity_id == entity_id).order_by(AuditEvent.created_at)
    rows = (await db_session.execute(stmt)).scalars().all()
    return [row.event_type for row in rows]


@pytest.mark.asyncio
async def test_successful_diagnosis_is_approved(client, db_session):
    case = await _seed_case(db_session)
    recommendation = RecoveryRecommendation(
        diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
        recovery_confidence=0.9,
        # Must be an EXECUTABLE_ACTION: APPROVED means "ready to execute",
        # so only an action the executor can actually perform belongs on
        # this path. See test_permitted_but_unexecutable_action_escalates.
        recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
        decision_explanation="Card declined; a payment link lets them retry.",
    )
    _override_ai_service(FakeAIProvider(recommendation=recommendation))

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")

    assert response.status_code == 200
    body = response.json()
    assert body["case_status"] == RecoveryCaseStatus.APPROVED.value
    assert body["decision_source"] == DecisionSource.AI.value
    assert body["policy_decision"] == PolicyDecisionType.ALLOW.value
    assert body["recommended_action"] == RecoveryAction.SEND_PAYMENT_LINK.value
    assert body["correlation_id"]


@pytest.mark.asyncio
async def test_permitted_but_unexecutable_action_escalates_instead_of_approving(client, db_session):
    """RETRY_PAYMENT passes every policy rule, but no executor implements
    it (Razorpay has no generic retry endpoint). Approving such a case
    would advertise an Execute action that could only ever fail, so it
    goes to human review at diagnosis time instead."""
    case = await _seed_case(db_session)
    recommendation = RecoveryRecommendation(
        diagnosis_category=DiagnosisCategory.TEMPORARY_FAILURE,
        recovery_confidence=0.95,
        recommended_action=RecoveryAction.RETRY_PAYMENT,
        decision_explanation="Transient failure, first attempt.",
    )
    _override_ai_service(FakeAIProvider(recommendation=recommendation))

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")

    assert response.status_code == 200
    body = response.json()
    # Policy said ALLOW — this is not a policy block, it's a capability gap.
    assert body["policy_decision"] == PolicyDecisionType.ALLOW.value
    assert body["policy_reason_codes"] == []
    assert body["case_status"] == RecoveryCaseStatus.ESCALATED.value
    assert body["recommended_action"] == RecoveryAction.RETRY_PAYMENT.value


@pytest.mark.asyncio
async def test_unexecutable_action_records_why_it_escalated(client, db_session):
    """The audit trail must distinguish 'policy blocked this' from
    'nothing can execute this' — they need different human follow-up."""
    case = await _seed_case(db_session)
    _override_ai_service(
        FakeAIProvider(
            recommendation=RecoveryRecommendation(
                diagnosis_category=DiagnosisCategory.TEMPORARY_FAILURE,
                recovery_confidence=0.95,
                recommended_action=RecoveryAction.RETRY_PAYMENT,
                decision_explanation="Transient failure.",
            )
        )
    )

    await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")

    stmt = (
        select(AuditEvent)
        .where(AuditEvent.entity_id == case.id)
        .where(AuditEvent.event_type == "recovery_case_status_changed")
        .order_by(AuditEvent.created_at)
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    final = rows[-1]
    assert final.payload["to_status"] == RecoveryCaseStatus.ESCALATED.value
    assert "no executor implementation" in (final.payload["reason"] or "")


@pytest.mark.asyncio
async def test_send_reminder_is_also_not_approvable_yet(client, db_session):
    """Guards the other action whose provider method exists but isn't
    wired into the execution flow."""
    case = await _seed_case(db_session)
    _override_ai_service(
        FakeAIProvider(
            recommendation=RecoveryRecommendation(
                diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
                recovery_confidence=0.95,
                recommended_action=RecoveryAction.SEND_REMINDER,
                decision_explanation="A nudge should be enough.",
            )
        )
    )

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")
    assert response.json()["case_status"] == RecoveryCaseStatus.ESCALATED.value


@pytest.mark.asyncio
async def test_low_confidence_recommendation_is_blocked_and_escalated(client, db_session):
    case = await _seed_case(db_session)
    recommendation = RecoveryRecommendation(
        diagnosis_category=DiagnosisCategory.TEMPORARY_FAILURE,
        recovery_confidence=0.1,  # below the policy engine's default threshold
        recommended_action=RecoveryAction.RETRY_PAYMENT,
        decision_explanation="Weak signal.",
    )
    _override_ai_service(FakeAIProvider(recommendation=recommendation))

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")

    assert response.status_code == 200
    body = response.json()
    assert body["policy_decision"] == PolicyDecisionType.BLOCK.value
    assert "confidence_below_threshold" in body["policy_reason_codes"]
    assert body["case_status"] == RecoveryCaseStatus.ESCALATED.value


@pytest.mark.asyncio
async def test_policy_conflict_retry_limit_reached_stops_the_case(client, db_session):
    # AI is confident and wants to retry, but this case already exhausted
    # its retry budget — the AI's confidence must not override that.
    case = await _seed_case(db_session, current_attempt_number=3)
    recommendation = RecoveryRecommendation(
        diagnosis_category=DiagnosisCategory.TEMPORARY_FAILURE,
        recovery_confidence=0.95,
        recommended_action=RecoveryAction.RETRY_PAYMENT,
        decision_explanation="Looks recoverable.",
    )
    _override_ai_service(FakeAIProvider(recommendation=recommendation))

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")

    assert response.status_code == 200
    body = response.json()
    assert body["policy_decision"] == PolicyDecisionType.BLOCK.value
    assert "max_retries_reached" in body["policy_reason_codes"]
    assert body["case_status"] == RecoveryCaseStatus.STOPPED.value


@pytest.mark.asyncio
async def test_timeout_falls_back_and_still_escalates_safely(client, db_session):
    case = await _seed_case(db_session)
    _override_ai_service(FakeAIProvider(raise_error=AIProviderTimeoutError("simulated timeout")))

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")

    assert response.status_code == 200
    body = response.json()
    assert body["decision_source"] == DecisionSource.FALLBACK.value
    assert body["recommended_action"] == RecoveryAction.ESCALATE.value
    assert body["case_status"] == RecoveryCaseStatus.ESCALATED.value


@pytest.mark.asyncio
async def test_provider_unavailable_falls_back_and_still_escalates_safely(client, db_session):
    case = await _seed_case(db_session)
    _override_ai_service(FakeAIProvider(raise_error=AIProviderUnavailableError("simulated outage")))

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")

    assert response.status_code == 200
    assert response.json()["case_status"] == RecoveryCaseStatus.ESCALATED.value


@pytest.mark.asyncio
async def test_malformed_ai_output_falls_back_safely(client, db_session):
    case = await _seed_case(db_session)
    _override_ai_service(FakeAIProvider(malformed=True))

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")

    assert response.status_code == 200
    body = response.json()
    assert body["decision_source"] == DecisionSource.FALLBACK.value
    assert body["case_status"] == RecoveryCaseStatus.ESCALATED.value


@pytest.mark.asyncio
async def test_ineligible_payment_skips_ai_entirely(client, db_session):
    case = await _seed_case(db_session, payment_status=PaymentStatus.CAPTURED)
    fake_provider = FakeAIProvider(
        recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.TEMPORARY_FAILURE,
            recovery_confidence=0.9,
            recommended_action=RecoveryAction.RETRY_PAYMENT,
            decision_explanation="n/a",
        )
    )
    _override_ai_service(fake_provider)

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")

    assert response.status_code == 200
    body = response.json()
    assert body["case_status"] == RecoveryCaseStatus.INELIGIBLE.value
    assert body["decision_source"] is None
    assert fake_provider.call_count == 0


@pytest.mark.asyncio
async def test_repeated_diagnose_request_is_rejected_not_reexecuted(client, db_session):
    case = await _seed_case(db_session)
    recommendation = RecoveryRecommendation(
        diagnosis_category=DiagnosisCategory.TEMPORARY_FAILURE,
        recovery_confidence=0.9,
        recommended_action=RecoveryAction.RETRY_PAYMENT,
        decision_explanation="ok",
    )
    fake_provider = FakeAIProvider(recommendation=recommendation)
    _override_ai_service(fake_provider)

    first = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")
    assert first.status_code == 200

    second = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")
    assert second.status_code == 409
    assert second.json()["error_code"] == "invalid_state_transition"

    # The AI was only ever consulted once, not twice.
    assert fake_provider.call_count == 1


@pytest.mark.asyncio
async def test_case_not_found_returns_404(client):
    _override_ai_service(FakeAIProvider(malformed=True))
    response = await client.post(f"/api/v1/recovery-cases/{uuid.uuid4()}/diagnose")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_full_audit_trail_is_recorded_for_a_successful_diagnosis(client, db_session):
    case = await _seed_case(db_session)
    recommendation = RecoveryRecommendation(
        diagnosis_category=DiagnosisCategory.TEMPORARY_FAILURE,
        recovery_confidence=0.9,
        recommended_action=RecoveryAction.RETRY_PAYMENT,
        decision_explanation="ok",
    )
    _override_ai_service(FakeAIProvider(recommendation=recommendation))

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")
    assert response.status_code == 200

    event_types = await _audit_event_types(db_session, case.id)
    assert event_types == [
        "diagnosis_requested",
        "recovery_case_status_changed",  # DISCOVERED -> ELIGIBLE
        "recovery_case_status_changed",  # ELIGIBLE -> DIAGNOSING
        "ai_diagnosis_created",
        "recovery_recommendation_created",
        "recovery_case_status_changed",  # DIAGNOSING -> RECOMMENDED
        "recovery_case_status_changed",  # RECOMMENDED -> POLICY_REVIEW
        "policy_evaluated",
        "recovery_case_status_changed",  # POLICY_REVIEW -> APPROVED
    ]


@pytest.mark.asyncio
async def test_fallback_audit_event_records_decision_source_and_failure_code(client, db_session):
    case = await _seed_case(db_session)
    _override_ai_service(FakeAIProvider(raise_error=AIProviderTimeoutError("simulated timeout")))

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/diagnose")
    assert response.status_code == 200

    stmt = select(AuditEvent).where(
        AuditEvent.entity_id == case.id, AuditEvent.event_type == "ai_diagnosis_created"
    )
    events = (await db_session.execute(stmt)).scalars().all()
    assert len(events) == 1
    payload = events[0].payload
    assert payload["decision_source"] == "fallback"
    assert payload["failure_code"] == "AIProviderTimeoutError"
    assert events[0].actor_type == ActorType.SYSTEM
