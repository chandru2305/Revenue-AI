"""POST /api/v1/orchestrator/cycle — the operator-triggered pass."""
from __future__ import annotations

import pytest

from app.ai.dependencies import get_ai_service
from app.ai.providers.fake import FakeAIProvider
from app.ai.schemas import RecoveryRecommendation
from app.ai.service import AIRecommendationService
from app.core.config import get_settings
from app.domain.enums import (
    DiagnosisCategory,
    PaymentMethodType,
    PaymentStatus,
    RecoveryAction,
    RecoveryCaseStatus,
)
from app.main import app
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.payments.dependencies import get_payment_provider
from app.payments.providers.fake import FakePaymentProvider

APPROVABLE = RecoveryRecommendation(
    diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
    recovery_confidence=0.9,
    recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
    decision_explanation="Insufficient funds; send a link.",
)


@pytest.fixture(autouse=True)
def _fake_providers():
    """Both external boundaries faked — same substitution the rest of the
    suite uses. Without the payment override the cycle would hit
    `_UnconfiguredPaymentProvider` (no Razorpay key in tests) and every
    execution would correctly escalate instead."""
    app.dependency_overrides[get_ai_service] = lambda: AIRecommendationService(
        FakeAIProvider(recommendation=APPROVABLE), model_name="fake", max_retries=0
    )
    app.dependency_overrides[get_payment_provider] = FakePaymentProvider
    yield
    app.dependency_overrides.pop(get_ai_service, None)
    app.dependency_overrides.pop(get_payment_provider, None)


async def _seed_case(db_session, *, amount=15_000):
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
        payment_id=payment.id, status=RecoveryCaseStatus.DISCOVERED, revenue_at_risk=amount
    )
    db_session.add(case)
    await db_session.flush()
    return case


@pytest.mark.asyncio
async def test_cycle_endpoint_returns_a_full_report(client, db_session):
    await _seed_case(db_session)
    await db_session.commit()

    response = await client.post("/api/v1/orchestrator/cycle")

    assert response.status_code == 200
    body = response.json()
    assert body["cases_diagnosed"] == 1
    assert body["approved"] == 1
    assert body["cases_executed"] == 0  # auto-execute defaults off
    assert body["auto_execute"] is False
    assert body["cycle_id"]
    assert body["duration_seconds"] >= 0
    assert len(body["outcomes"]) == 1


@pytest.mark.asyncio
async def test_cycle_endpoint_defaults_to_the_configured_auto_execute(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "orchestrator_auto_execute", False)
    await _seed_case(db_session)
    await db_session.commit()

    body = (await client.post("/api/v1/orchestrator/cycle")).json()

    assert body["auto_execute"] is False
    assert body["cases_executed"] == 0


@pytest.mark.asyncio
async def test_auto_execute_can_be_overridden_per_request(client, db_session):
    await _seed_case(db_session)
    await db_session.commit()

    body = (await client.post("/api/v1/orchestrator/cycle?auto_execute=true")).json()

    assert body["auto_execute"] is True
    assert body["cases_executed"] == 1


@pytest.mark.asyncio
async def test_cycle_on_an_empty_database_is_a_clean_no_op(client):
    body = (await client.post("/api/v1/orchestrator/cycle")).json()

    assert body["cases_discovered"] == 0
    assert body["cases_diagnosed"] == 0
    assert body["cases_failed"] == 0


@pytest.mark.asyncio
async def test_cycle_endpoint_is_behind_the_api_key(client, monkeypatch):
    """It triggers provider calls, so it must not be publicly reachable."""
    monkeypatch.setattr(get_settings(), "api_key", "rk_test_key")
    assert (await client.post("/api/v1/orchestrator/cycle")).status_code == 401


@pytest.mark.asyncio
async def test_status_on_a_fresh_database_is_idle_with_no_cycles(client):
    body = (await client.get("/api/v1/orchestrator/status")).json()

    assert body["agent_state"] == "idle"
    assert body["running"] is False
    assert body["cycles_completed"] == 0
    assert body["last_cycle"] is None


@pytest.mark.asyncio
async def test_status_reflects_the_last_cycle_from_the_audit_trail(client, db_session):
    await _seed_case(db_session)
    await db_session.commit()
    await client.post("/api/v1/orchestrator/cycle")

    body = (await client.get("/api/v1/orchestrator/status")).json()

    assert body["cycles_completed"] == 1
    assert body["last_cycle"] is not None
    assert body["last_cycle"]["cases_diagnosed"] == 1
    assert body["last_cycle"]["approved"] == 1
    assert body["last_cycle"]["completed_at"]
    # The fake provider still returns an AI-sourced outcome, so the sampled
    # window sees one diagnosis with a measured latency.
    assert body["recent_ai_diagnoses"] == 1
    assert isinstance(body["average_ai_latency_ms"], int | float)


@pytest.mark.asyncio
async def test_status_is_behind_the_api_key(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_key", "rk_test_key")
    assert (await client.get("/api/v1/orchestrator/status")).status_code == 401
