"""End-to-end tests for POST /api/v1/recovery-cases/{id}/execute.

Exercises the full orchestration (app.services.execution_service) through
the real HTTP route, with a FakePaymentProvider swapped in via FastAPI's
dependency-override mechanism — no network calls, fully deterministic.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.enums import (
    PaymentMethodType,
    PaymentStatus,
    RecoveryAction,
    RecoveryAttemptStatus,
    RecoveryCaseStatus,
    RecoveryPaymentRequestStatus,
)
from app.domain.providers.base import (
    PaymentLinkSnapshot,
    PaymentProviderAmbiguousError,
    PaymentProviderAuthError,
    PaymentProviderTimeoutError,
    PaymentProviderValidationError,
)
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_attempt import RecoveryAttempt
from app.models.recovery_case import RecoveryCase
from app.models.recovery_payment_request import RecoveryPaymentRequest
from app.payments.dependencies import get_payment_provider
from app.payments.providers.fake import FakePaymentProvider


async def _seed_approved_case(
    db_session,
    *,
    amount=15_000,
    recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
    current_attempt_number=0,
    customer_contact_count=0,
):
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
        payment_id=payment.id,
        status=RecoveryCaseStatus.APPROVED,
        revenue_at_risk=amount,
        eligible=True,
        recommended_action=recommended_action,
        recovery_confidence=0.9,
        current_attempt_number=current_attempt_number,
        customer_contact_count=customer_contact_count,
        policy_version="v1",
    )
    db_session.add(case)
    await db_session.flush()
    return case


def _override_provider(provider: FakePaymentProvider) -> None:
    app.dependency_overrides[get_payment_provider] = lambda: provider


async def _audit_event_types(db_session, entity_id: uuid.UUID) -> list[str]:
    stmt = select(AuditEvent).where(AuditEvent.entity_id == entity_id).order_by(AuditEvent.created_at)
    rows = (await db_session.execute(stmt)).scalars().all()
    return [row.event_type for row in rows]


@pytest.mark.asyncio
async def test_successful_execution_creates_payment_link(client, db_session):
    case = await _seed_approved_case(db_session)
    provider = FakePaymentProvider()
    _override_provider(provider)

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")

    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is True
    assert body["case_status"] == RecoveryCaseStatus.EXECUTING.value
    assert body["provider_reference"].startswith("plink_fake_")
    assert body["amount"] == 15_000
    assert provider.create_call_count == 1

    # The canonical amount, never anything else, was sent to the provider.
    stmt = select(RecoveryPaymentRequest).where(RecoveryPaymentRequest.recovery_case_id == case.id)
    payment_request = (await db_session.execute(stmt)).scalar_one()
    assert payment_request.amount == 15_000

    stmt = select(RecoveryAttempt).where(RecoveryAttempt.recovery_case_id == case.id)
    attempt = (await db_session.execute(stmt)).scalar_one()
    assert attempt.status == RecoveryAttemptStatus.SUCCEEDED
    assert attempt.amount == 15_000


@pytest.mark.asyncio
async def test_execute_requires_approved_status(client, db_session):
    case = await _seed_approved_case(db_session)
    case.status = RecoveryCaseStatus.DISCOVERED
    await db_session.flush()
    _override_provider(FakePaymentProvider())

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")

    assert response.status_code == 409
    assert response.json()["error_code"] == "invalid_state_transition"


@pytest.mark.asyncio
async def test_repeated_execution_is_rejected_not_reexecuted(client, db_session):
    case = await _seed_approved_case(db_session)
    provider = FakePaymentProvider()
    _override_provider(provider)

    first = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")
    assert first.status_code == 200

    second = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")
    assert second.status_code == 409
    assert provider.create_call_count == 1  # never called a second time


@pytest.mark.asyncio
async def test_policy_recheck_blocks_execution_and_escalates(client, db_session):
    # Approved earlier, but the contact budget is now exhausted — the
    # re-check must catch this even though the case was already APPROVED.
    # (MAX_RETRIES_REACHED only gates RETRY_PAYMENT, not SEND_PAYMENT_LINK
    # — see app/domain/policy.py — so the contact cap is what applies here.)
    case = await _seed_approved_case(db_session, customer_contact_count=2)
    provider = FakePaymentProvider()
    _override_provider(provider)

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")

    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False
    assert body["case_status"] == RecoveryCaseStatus.ESCALATED.value
    assert body["policy_decision"] == "block"
    assert "max_contacts_reached" in body["policy_reason_codes"]
    assert provider.create_call_count == 0  # never reached the provider


@pytest.mark.asyncio
async def test_unsupported_action_is_escalated_not_faked(client, db_session):
    case = await _seed_approved_case(db_session, recommended_action=RecoveryAction.RETRY_PAYMENT)
    provider = FakePaymentProvider()
    _override_provider(provider)

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")

    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False
    assert body["case_status"] == RecoveryCaseStatus.ESCALATED.value
    assert "not yet implemented" in body["reason"]
    assert provider.create_call_count == 0


@pytest.mark.asyncio
async def test_provider_validation_error_escalates(client, db_session):
    case = await _seed_approved_case(db_session)
    provider = FakePaymentProvider(create_error=PaymentProviderValidationError("bad amount"))
    _override_provider(provider)

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")

    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False
    assert body["case_status"] == RecoveryCaseStatus.ESCALATED.value

    stmt = select(RecoveryAttempt).where(RecoveryAttempt.recovery_case_id == case.id)
    attempt = (await db_session.execute(stmt)).scalar_one()
    assert attempt.status == RecoveryAttemptStatus.FAILED
    assert attempt.failure_code == "provider_validation_error"


@pytest.mark.asyncio
async def test_provider_timeout_escalates_gracefully(client, db_session):
    case = await _seed_approved_case(db_session)
    provider = FakePaymentProvider(create_error=PaymentProviderTimeoutError("connect timed out"))
    _override_provider(provider)

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")

    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False
    assert body["case_status"] == RecoveryCaseStatus.ESCALATED.value

    stmt = select(RecoveryAttempt).where(RecoveryAttempt.recovery_case_id == case.id)
    attempt = (await db_session.execute(stmt)).scalar_one()
    assert attempt.failure_code == "provider_timeout"


@pytest.mark.asyncio
async def test_provider_auth_error_escalates(client, db_session):
    case = await _seed_approved_case(db_session)
    _override_provider(FakePaymentProvider(create_error=PaymentProviderAuthError("bad key")))

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")

    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False
    assert body["case_status"] == RecoveryCaseStatus.ESCALATED.value


@pytest.mark.asyncio
async def test_ambiguous_result_reconciled_as_success(client, db_session):
    reconciled = PaymentLinkSnapshot(
        provider_reference="plink_actually_created",
        short_url="https://rzp.io/i/actual",
        status=RecoveryPaymentRequestStatus.CREATED,
        amount=15_000,
        amount_paid=0,
        currency="INR",
        reference_id="whatever",
        expires_at=datetime.now(UTC) + timedelta(hours=72),
    )
    case = await _seed_approved_case(db_session)
    provider = FakePaymentProvider(
        create_error=PaymentProviderAmbiguousError("timeout, unclear if created"),
        reconciled_link=reconciled,
    )
    _override_provider(provider)

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")

    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is True
    assert body["provider_reference"] == "plink_actually_created"
    assert body["case_status"] == RecoveryCaseStatus.EXECUTING.value
    assert provider.find_by_reference_call_count == 1


@pytest.mark.asyncio
async def test_ambiguous_result_unresolved_escalates_without_duplicate(client, db_session):
    case = await _seed_approved_case(db_session)
    provider = FakePaymentProvider(
        create_error=PaymentProviderAmbiguousError("timeout, unclear if created"),
        reconciled_link=None,
    )
    _override_provider(provider)

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")

    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False
    assert body["case_status"] == RecoveryCaseStatus.ESCALATED.value
    assert provider.find_by_reference_call_count == 1
    assert provider.create_call_count == 1  # never retried the create itself

    stmt = select(RecoveryAttempt).where(RecoveryAttempt.recovery_case_id == case.id)
    attempt = (await db_session.execute(stmt)).scalar_one()
    assert attempt.failure_code == "ambiguous_result"


@pytest.mark.asyncio
async def test_case_not_found_returns_404(client):
    _override_provider(FakePaymentProvider())
    response = await client.post(f"/api/v1/recovery-cases/{uuid.uuid4()}/execute")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_full_audit_trail_for_successful_execution(client, db_session):
    case = await _seed_approved_case(db_session)
    _override_provider(FakePaymentProvider())

    response = await client.post(f"/api/v1/recovery-cases/{case.id}/execute")
    assert response.status_code == 200

    event_types = await _audit_event_types(db_session, case.id)
    assert event_types == [
        "execution_requested",
        "policy_rechecked",
        "recovery_case_status_changed",  # APPROVED -> EXECUTING
        "payment_link_created",
    ]
