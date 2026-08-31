"""Failed-payment ingestion: the workflow's entry point.

Covers the three ways a recovery case can be opened —
`POST /payments` (auto), `POST /recovery-cases` (explicit, idempotent),
and `POST /recovery-cases/discover` (sweep) — plus that a case created
this way flows straight into the existing diagnosis pipeline.
"""
from __future__ import annotations

import uuid

import pytest

from app.domain.enums import PaymentMethodType, PaymentStatus, RecoveryCaseStatus
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase


async def _seed_payment(db_session, *, status=PaymentStatus.FAILED, amount=15_000) -> Payment:
    customer = Customer()
    db_session.add(customer)
    await db_session.flush()
    payment = Payment(
        customer_id=customer.id,
        amount=amount,
        currency="INR",
        status=status,
        payment_method_type=PaymentMethodType.CARD,
        attempt_number=1,
    )
    db_session.add(payment)
    await db_session.flush()
    return payment


@pytest.mark.asyncio
async def test_ingest_failed_payment_auto_creates_recovery_case(client):
    response = await client.post(
        "/api/v1/payments",
        json={
            "customer_reference": "cust_ext_1",
            "amount": 42_000,
            "failure_reason": "insufficient_funds",
            "payment_method_type": "card",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["recovery_case_id"] is not None
    assert body["recovery_case_status"] == RecoveryCaseStatus.DISCOVERED.value

    case_resp = await client.get(f"/api/v1/recovery-cases/{body['recovery_case_id']}")
    assert case_resp.status_code == 200
    assert case_resp.json()["revenue_at_risk"] == 42_000


@pytest.mark.asyncio
async def test_ingest_with_auto_create_disabled_leaves_no_case(client):
    response = await client.post(
        "/api/v1/payments",
        json={"amount": 10_000, "auto_create_case": False},
    )
    assert response.status_code == 201
    assert response.json()["recovery_case_id"] is None


@pytest.mark.asyncio
async def test_ingest_non_failed_payment_creates_no_case(client):
    response = await client.post(
        "/api/v1/payments",
        json={"amount": 10_000, "status": "captured"},
    )
    assert response.status_code == 201
    assert response.json()["recovery_case_id"] is None


@pytest.mark.asyncio
async def test_ingest_reuses_customer_by_external_reference(client, db_session):
    from sqlalchemy import func, select

    for _ in range(2):
        resp = await client.post(
            "/api/v1/payments",
            json={"customer_reference": "cust_shared", "amount": 5_000},
        )
        assert resp.status_code == 201

    customer_count = (
        await db_session.execute(
            select(func.count()).select_from(Customer).where(Customer.external_reference == "cust_shared")
        )
    ).scalar_one()
    assert customer_count == 1

    customer = (
        await db_session.execute(
            select(Customer).where(Customer.external_reference == "cust_shared")
        )
    ).scalar_one()
    assert customer.total_payments_count == 2
    assert customer.total_failed_payments_count == 2


@pytest.mark.asyncio
async def test_create_case_for_missing_payment_returns_404(client):
    response = await client.post(
        "/api/v1/recovery-cases", json={"payment_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_create_case_for_non_failed_payment_returns_422(client, db_session):
    payment = await _seed_payment(db_session, status=PaymentStatus.CAPTURED)
    await db_session.commit()

    response = await client.post(
        "/api/v1/recovery-cases", json={"payment_id": str(payment.id)}
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "validation_failed"


@pytest.mark.asyncio
async def test_create_case_is_idempotent(client, db_session):
    payment = await _seed_payment(db_session)
    await db_session.commit()

    first = await client.post("/api/v1/recovery-cases", json={"payment_id": str(payment.id)})
    assert first.status_code == 201
    assert first.json()["created"] is True

    second = await client.post("/api/v1/recovery-cases", json={"payment_id": str(payment.id)})
    assert second.status_code == 201
    assert second.json()["created"] is False
    assert second.json()["recovery_case_id"] == first.json()["recovery_case_id"]

    from sqlalchemy import func, select

    case_count = (
        await db_session.execute(
            select(func.count()).select_from(RecoveryCase).where(RecoveryCase.payment_id == payment.id)
        )
    ).scalar_one()
    assert case_count == 1


@pytest.mark.asyncio
async def test_discover_opens_cases_only_for_uncased_failed_payments(client, db_session):
    failed_uncased = await _seed_payment(db_session)
    failed_with_case = await _seed_payment(db_session)
    captured = await _seed_payment(db_session, status=PaymentStatus.CAPTURED)
    db_session.add(
        RecoveryCase(
            payment_id=failed_with_case.id,
            status=RecoveryCaseStatus.DISCOVERED,
            revenue_at_risk=failed_with_case.amount,
        )
    )
    await db_session.commit()

    response = await client.post("/api/v1/recovery-cases/discover")
    assert response.status_code == 200
    body = response.json()
    assert body["scanned"] == 1
    assert body["created"] == 1
    assert body["skipped_existing"] == 1  # failed_with_case
    assert len(body["case_ids"]) == 1

    # The captured payment was never a candidate.
    from sqlalchemy import select

    captured_case = (
        await db_session.execute(select(RecoveryCase).where(RecoveryCase.payment_id == captured.id))
    ).scalar_one_or_none()
    assert captured_case is None

    # Re-running the sweep is a no-op.
    again = await client.post("/api/v1/recovery-cases/discover")
    assert again.json()["created"] == 0
    assert failed_uncased.id  # referenced for clarity


@pytest.mark.asyncio
async def test_recovery_case_created_audit_event_is_recorded(client):
    ingest = await client.post("/api/v1/payments", json={"amount": 7_000})
    case_id = ingest.json()["recovery_case_id"]

    events = await client.get("/api/v1/audit-events", params={"entity_id": case_id})
    assert events.status_code == 200
    types = {e["event_type"] for e in events.json()["items"]}
    assert "recovery_case_created" in types


@pytest.mark.asyncio
async def test_ingested_case_can_be_diagnosed_end_to_end(client):
    """The whole point: a case opened by ingestion is a normal case the
    existing pipeline picks up with no special-casing."""
    ingest = await client.post(
        "/api/v1/payments",
        json={"amount": 12_000, "failure_reason": "gateway_timeout"},
    )
    case_id = ingest.json()["recovery_case_id"]

    diagnose = await client.post(f"/api/v1/recovery-cases/{case_id}/diagnose")
    assert diagnose.status_code == 200
    # No Gemini key in the test env -> safe fallback -> ESCALATED, never a crash.
    assert diagnose.json()["case_status"] in {
        RecoveryCaseStatus.APPROVED.value,
        RecoveryCaseStatus.ESCALATED.value,
        RecoveryCaseStatus.STOPPED.value,
    }
