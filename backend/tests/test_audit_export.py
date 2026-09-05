"""GET /api/v1/audit-events/export — CSV/JSON downloads of the audit trail.

Read-only: proves the endpoint reproduces real rows in real order, honors
the same filters as the list endpoint, redacts anything credential-shaped,
and never fabricates a field an event doesn't carry.
"""
from __future__ import annotations

import csv
import io
import json
import uuid

import pytest

from app.core.config import get_settings
from app.domain.enums import ActorType, PaymentMethodType, PaymentStatus, RecoveryCaseStatus
from app.models.audit_event import AuditEvent
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase


async def _seed_case_with_events(db_session):
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
        payment_id=payment.id, status=RecoveryCaseStatus.APPROVED, revenue_at_risk=15_000
    )
    db_session.add(case)
    await db_session.flush()

    db_session.add_all(
        [
            AuditEvent(
                entity_type="recovery_case",
                entity_id=case.id,
                event_type="recovery_case_created",
                actor_type=ActorType.SYSTEM,
                payload={"payment_id": str(payment.id), "revenue_at_risk": 15_000},
                correlation_id="corr-export-1",
            ),
            AuditEvent(
                entity_type="recovery_case",
                entity_id=case.id,
                event_type="ai_diagnosis_created",
                actor_type=ActorType.AI,
                payload={
                    "decision_source": "ai",
                    "diagnosis_category": "customer_side_failure",
                    "model": "openai/gpt-oss-120b",
                    "latency_ms": 1234.5,
                },
                correlation_id="corr-export-1",
            ),
            AuditEvent(
                entity_type="recovery_case",
                entity_id=case.id,
                event_type="policy_evaluated",
                actor_type=ActorType.POLICY_ENGINE,
                payload={
                    "decision": "allow",
                    "reason_codes": [],
                    "policy_version": "v1",
                    "proposed_action": "send_payment_link",
                },
                correlation_id="corr-export-1",
            ),
            AuditEvent(
                entity_type="recovery_case",
                entity_id=case.id,
                event_type="recovery_case_status_changed",
                actor_type=ActorType.SYSTEM,
                payload={"from_status": "policy_review", "to_status": "approved", "reason": None},
                correlation_id="corr-export-1",
            ),
        ]
    )
    await db_session.flush()
    return case, payment


@pytest.mark.asyncio
async def test_csv_export_has_header_and_one_row_per_event(client, db_session):
    case, _ = await _seed_case_with_events(db_session)

    response = await client.get(
        "/api/v1/audit-events/export", params={"format": "csv", "entity_id": str(case.id)}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert "audit_trail.csv" in response.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 4
    assert {r["event_type"] for r in rows} == {
        "recovery_case_created",
        "ai_diagnosis_created",
        "policy_evaluated",
        "recovery_case_status_changed",
    }


@pytest.mark.asyncio
async def test_csv_export_preserves_chronological_order(client, db_session):
    case, _ = await _seed_case_with_events(db_session)

    response = await client.get(
        "/api/v1/audit-events/export", params={"format": "csv", "entity_id": str(case.id)}
    )
    rows = list(csv.DictReader(io.StringIO(response.text)))

    assert [r["event_type"] for r in rows] == [
        "recovery_case_created",
        "ai_diagnosis_created",
        "policy_evaluated",
        "recovery_case_status_changed",
    ]
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_csv_export_flattens_known_fields_without_inventing_them(client, db_session):
    case, payment = await _seed_case_with_events(db_session)

    response = await client.get(
        "/api/v1/audit-events/export", params={"format": "csv", "entity_id": str(case.id)}
    )
    rows = list(csv.DictReader(io.StringIO(response.text)))

    ai_row = next(r for r in rows if r["event_type"] == "ai_diagnosis_created")
    assert ai_row["ai_model"] == "openai/gpt-oss-120b"
    assert ai_row["ai_decision_source"] == "ai"
    assert ai_row["diagnosis_category"] == "customer_side_failure"
    assert ai_row["ai_latency_ms"] == "1234.5"
    # Fields this event type doesn't carry stay blank, never fabricated.
    assert ai_row["policy_decision"] == ""
    assert ai_row["from_status"] == ""

    policy_row = next(r for r in rows if r["event_type"] == "policy_evaluated")
    assert policy_row["policy_decision"] == "allow"
    assert policy_row["policy_version"] == "v1"
    assert policy_row["proposed_action"] == "send_payment_link"

    transition_row = next(r for r in rows if r["event_type"] == "recovery_case_status_changed")
    assert transition_row["from_status"] == "policy_review"
    assert transition_row["to_status"] == "approved"
    assert transition_row["final_outcome"] == "approved" or transition_row["final_outcome"] == ""
    # "approved" is not terminal, so final_outcome must stay blank for it.
    assert transition_row["final_outcome"] == ""

    created_row = next(r for r in rows if r["event_type"] == "recovery_case_created")
    assert created_row["payment_id"] == str(payment.id)
    assert created_row["case_id"] == str(case.id)
    assert created_row["amount"] == "15000"


@pytest.mark.asyncio
async def test_json_export_matches_csv_row_count_and_is_valid_json(client, db_session):
    case, _ = await _seed_case_with_events(db_session)

    response = await client.get(
        "/api/v1/audit-events/export", params={"format": "json", "entity_id": str(case.id)}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "audit_trail.json" in response.headers["content-disposition"]

    body = json.loads(response.text)
    assert isinstance(body, list)
    assert len(body) == 4
    assert all("payload_json" in row for row in body)


@pytest.mark.asyncio
async def test_export_respects_the_same_filters_as_the_list_endpoint(client, db_session):
    case, _ = await _seed_case_with_events(db_session)

    all_rows = (
        await client.get("/api/v1/audit-events/export", params={"format": "json"})
    ).json()
    filtered_rows = (
        await client.get(
            "/api/v1/audit-events/export",
            params={"format": "json", "event_type": "policy_evaluated"},
        )
    ).json()

    assert len(all_rows) == 4
    assert len(filtered_rows) == 1
    assert filtered_rows[0]["event_type"] == "policy_evaluated"

    by_correlation = (
        await client.get(
            "/api/v1/audit-events/export",
            params={"format": "json", "correlation_id": "corr-export-1"},
        )
    ).json()
    assert len(by_correlation) == 4
    assert case.id  # referenced for clarity


@pytest.mark.asyncio
async def test_export_never_leaks_a_credential_shaped_field(client, db_session):
    """Defense in depth: even if a future call site accidentally recorded
    something secret-shaped in a payload, the export must strip it."""
    customer = Customer()
    db_session.add(customer)
    await db_session.flush()
    payment = Payment(
        customer_id=customer.id,
        amount=1_000,
        currency="INR",
        status=PaymentStatus.FAILED,
        payment_method_type=PaymentMethodType.CARD,
        attempt_number=1,
    )
    db_session.add(payment)
    await db_session.flush()
    case = RecoveryCase(payment_id=payment.id, status=RecoveryCaseStatus.DISCOVERED, revenue_at_risk=1_000)
    db_session.add(case)
    await db_session.flush()
    db_session.add(
        AuditEvent(
            entity_type="recovery_case",
            entity_id=case.id,
            event_type="hypothetical_leak",
            actor_type=ActorType.SYSTEM,
            payload={
                "webhook_secret": "should-never-appear",
                "razorpay_api_key": "also-hidden",
                "note": "kept",
            },
        )
    )
    await db_session.flush()

    response = await client.get(
        "/api/v1/audit-events/export", params={"format": "json", "entity_id": str(case.id)}
    )
    body = response.json()
    raw_text = response.text

    assert "should-never-appear" not in raw_text
    assert "also-hidden" not in raw_text
    assert "kept" in body[0]["payload_json"]


@pytest.mark.asyncio
async def test_export_on_empty_database_returns_an_empty_csv_header_only(client):
    response = await client.get("/api/v1/audit-events/export", params={"format": "csv"})
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows == []


@pytest.mark.asyncio
async def test_export_never_modifies_audit_records(client, db_session):
    case, _ = await _seed_case_with_events(db_session)
    before = (
        await client.get("/api/v1/audit-events", params={"entity_id": str(case.id), "page_size": 100})
    ).json()

    await client.get("/api/v1/audit-events/export", params={"format": "csv"})
    await client.get("/api/v1/audit-events/export", params={"format": "json"})

    after = (
        await client.get("/api/v1/audit-events", params={"entity_id": str(case.id), "page_size": 100})
    ).json()
    assert before == after


@pytest.mark.asyncio
async def test_export_is_behind_the_api_key(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_key", "rk_test_key")
    response = await client.get("/api/v1/audit-events/export")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_export_rejects_an_unknown_format(client):
    response = await client.get("/api/v1/audit-events/export", params={"format": "xml"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_export_by_unknown_entity_id_is_empty_not_an_error(client):
    response = await client.get(
        "/api/v1/audit-events/export", params={"format": "json", "entity_id": str(uuid.uuid4())}
    )
    assert response.status_code == 200
    assert response.json() == []
