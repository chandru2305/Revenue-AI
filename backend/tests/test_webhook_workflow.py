"""End-to-end tests for POST /api/v1/webhooks/razorpay and
app.services.webhook_service — signature verification, deduplication, and
the only path to RECOVERED.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.domain.enums import (
    PaymentMethodType,
    PaymentStatus,
    RecoveryAction,
    RecoveryAttemptStatus,
    RecoveryCaseStatus,
    RecoveryPaymentRequestStatus,
)
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.processed_webhook_event import ProcessedWebhookEvent
from app.models.recovery_attempt import RecoveryAttempt
from app.models.recovery_case import RecoveryCase
from app.models.recovery_payment_request import RecoveryPaymentRequest

WEBHOOK_SECRET = "whsec_test_secret"


def _sign(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def _seed_executing_case(db_session, *, amount=15_000):
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
        status=RecoveryCaseStatus.EXECUTING,
        revenue_at_risk=amount,
        eligible=True,
        recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
        recovery_confidence=0.9,
    )
    db_session.add(case)
    await db_session.flush()

    attempt = RecoveryAttempt(
        recovery_case_id=case.id,
        action=RecoveryAction.SEND_PAYMENT_LINK,
        status=RecoveryAttemptStatus.SUCCEEDED,
        provider="razorpay",
        amount=amount,
        currency="INR",
        idempotency_key=str(uuid.uuid4()),
    )
    db_session.add(attempt)
    await db_session.flush()

    payment_request = RecoveryPaymentRequest(
        recovery_case_id=case.id,
        recovery_attempt_id=attempt.id,
        provider="razorpay",
        provider_reference="plink_test123",
        reference_id=attempt.idempotency_key,
        short_url="https://rzp.io/i/test",
        amount=amount,
        amount_paid=0,
        currency="INR",
        status=RecoveryPaymentRequestStatus.CREATED,
    )
    db_session.add(payment_request)
    await db_session.flush()

    return case, attempt, payment_request


def _payment_link_event(event_type: str, *, payment_link_id: str, status: str, amount_paid: int) -> dict:
    return {
        "event": event_type,
        "payload": {
            "payment_link": {"entity": {"id": payment_link_id, "status": status, "amount_paid": amount_paid}},
            "payment": {"entity": {"id": "pay_test123"}},
        },
    }


@pytest.fixture(autouse=True)
def _configure_webhook_secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "razorpay_webhook_secret", WEBHOOK_SECRET)


@pytest.mark.asyncio
async def test_valid_paid_webhook_recovers_the_case(client, db_session):
    case, _attempt, payment_request = await _seed_executing_case(db_session)
    body = json.dumps(
        _payment_link_event(
            "payment_link.paid", payment_link_id=payment_request.provider_reference, status="paid",
            amount_paid=15_000,
        )
    ).encode()

    response = await client.post(
        "/api/v1/webhooks/razorpay",
        content=body,
        headers={"x-razorpay-signature": _sign(body)},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "processed"

    await db_session.refresh(case)
    assert case.status == RecoveryCaseStatus.RECOVERED
    assert case.recovered_amount == 15_000


@pytest.mark.asyncio
async def test_expired_webhook_fails_the_case_not_recovers_it(client, db_session):
    case, _attempt, payment_request = await _seed_executing_case(db_session)
    body = json.dumps(
        _payment_link_event(
            "payment_link.expired", payment_link_id=payment_request.provider_reference, status="expired",
            amount_paid=0,
        )
    ).encode()

    response = await client.post(
        "/api/v1/webhooks/razorpay", content=body, headers={"x-razorpay-signature": _sign(body)}
    )

    assert response.status_code == 200
    await db_session.refresh(case)
    assert case.status == RecoveryCaseStatus.FAILED
    assert case.recovered_amount == 0


@pytest.mark.asyncio
async def test_partial_payment_never_marks_the_case_recovered(client, db_session):
    # accept_partial is False when creating the link (see
    # execution_service.py), so this shouldn't happen in practice — but
    # the webhook handler must not trust that and must independently
    # refuse to call a partial amount "recovered" if it ever does.
    case, _attempt, payment_request = await _seed_executing_case(db_session, amount=15_000)
    body = json.dumps(
        _payment_link_event(
            "payment_link.partially_paid",
            payment_link_id=payment_request.provider_reference,
            status="partially_paid",
            amount_paid=5_000,
        )
    ).encode()

    response = await client.post(
        "/api/v1/webhooks/razorpay", content=body, headers={"x-razorpay-signature": _sign(body)}
    )

    assert response.status_code == 200
    await db_session.refresh(case)
    assert case.status != RecoveryCaseStatus.RECOVERED
    assert case.recovered_amount == 0
    assert case.recovered_amount <= case.revenue_at_risk


@pytest.mark.asyncio
async def test_invalid_signature_is_rejected(client, db_session):
    case, _attempt, payment_request = await _seed_executing_case(db_session)
    body = json.dumps(
        _payment_link_event(
            "payment_link.paid", payment_link_id=payment_request.provider_reference, status="paid",
            amount_paid=15_000,
        )
    ).encode()

    response = await client.post(
        "/api/v1/webhooks/razorpay", content=body, headers={"x-razorpay-signature": "0" * 64}
    )

    assert response.status_code == 401
    await db_session.refresh(case)
    assert case.status == RecoveryCaseStatus.EXECUTING  # untouched


@pytest.mark.asyncio
async def test_duplicate_webhook_delivery_does_not_double_count_revenue(client, db_session):
    case, _attempt, payment_request = await _seed_executing_case(db_session)
    body = json.dumps(
        _payment_link_event(
            "payment_link.paid", payment_link_id=payment_request.provider_reference, status="paid",
            amount_paid=15_000,
        )
    ).encode()
    signature = _sign(body)

    first = await client.post(
        "/api/v1/webhooks/razorpay", content=body, headers={"x-razorpay-signature": signature}
    )
    assert first.status_code == 200
    assert first.json()["status"] == "processed"

    second = await client.post(
        "/api/v1/webhooks/razorpay", content=body, headers={"x-razorpay-signature": signature}
    )
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    await db_session.refresh(case)
    assert case.recovered_amount == 15_000  # not 30_000


@pytest.mark.asyncio
async def test_unmatched_payment_link_is_ignored_not_an_error(client, db_session):
    body = json.dumps(
        _payment_link_event(
            "payment_link.paid", payment_link_id="plink_never_created_by_us", status="paid", amount_paid=100
        )
    ).encode()

    response = await client.post(
        "/api/v1/webhooks/razorpay", content=body, headers={"x-razorpay-signature": _sign(body)}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_webhook_creates_exactly_one_dedup_record(client, db_session):
    case, _attempt, payment_request = await _seed_executing_case(db_session)
    body = json.dumps(
        _payment_link_event(
            "payment_link.paid", payment_link_id=payment_request.provider_reference, status="paid",
            amount_paid=15_000,
        )
    ).encode()

    await client.post(
        "/api/v1/webhooks/razorpay", content=body, headers={"x-razorpay-signature": _sign(body)}
    )

    stmt = select(ProcessedWebhookEvent)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 1
