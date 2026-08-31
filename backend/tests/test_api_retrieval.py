import uuid

import pytest

from app.domain.enums import (
    ActorType,
    PaymentMethodType,
    PaymentStatus,
    RecoveryCaseStatus,
)
from app.models.audit_event import AuditEvent
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase


async def _seed_payment_with_case(db_session):
    customer = Customer()
    db_session.add(customer)
    await db_session.flush()

    payment = Payment(
        customer_id=customer.id,
        amount=15_000,
        currency="INR",
        status=PaymentStatus.FAILED,
        payment_method_type=PaymentMethodType.CARD,
        attempt_number=1,
    )
    db_session.add(payment)
    await db_session.flush()

    case = RecoveryCase(
        payment_id=payment.id,
        status=RecoveryCaseStatus.DISCOVERED,
        revenue_at_risk=15_000,
        eligible=True,
    )
    db_session.add(case)
    await db_session.flush()
    return customer, payment, case


@pytest.mark.asyncio
async def test_list_payments_returns_seeded_payment(client, db_session):
    _, payment, _ = await _seed_payment_with_case(db_session)

    response = await client.get("/api/v1/payments")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(payment.id)


@pytest.mark.asyncio
async def test_list_payments_empty_state(client):
    response = await client.get("/api/v1/payments")
    assert response.status_code == 200
    assert response.json() == {"items": [], "page": 1, "page_size": 20, "total": 0}


@pytest.mark.asyncio
async def test_pagination_rejects_oversized_page_size(client):
    response = await client.get("/api/v1/payments", params={"page_size": 1000})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_recovery_case_detail(client, db_session):
    _, _, case = await _seed_payment_with_case(db_session)

    response = await client.get(f"/api/v1/recovery-cases/{case.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(case.id)
    assert body["status"] == RecoveryCaseStatus.DISCOVERED.value
    assert body["attempts"] == []


@pytest.mark.asyncio
async def test_get_recovery_case_not_found_returns_404(client):
    response = await client.get(f"/api/v1/recovery-cases/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_list_audit_events(client, db_session):
    _, _, case = await _seed_payment_with_case(db_session)
    db_session.add(
        AuditEvent(
            entity_type="recovery_case",
            entity_id=case.id,
            event_type="case_discovered",
            actor_type=ActorType.SYSTEM,
            payload={"revenue_at_risk": 15_000},
        )
    )
    await db_session.flush()

    response = await client.get("/api/v1/audit-events", params={"entity_id": str(case.id)})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "case_discovered"


@pytest.mark.asyncio
async def test_evaluation_summary_empty_state_when_no_report(client, tmp_path, monkeypatch):
    # Point the service at an empty directory so this test is independent of
    # whether a real evaluation report has been generated on this machine.
    from app.services import evaluation_service

    monkeypatch.setattr(evaluation_service, "REPORTS_DIR", tmp_path / "no-reports-here")

    response = await client.get("/api/v1/evaluation/summary")
    assert response.status_code == 200
    assert response.json()["status"] == "no_evaluation_run"
