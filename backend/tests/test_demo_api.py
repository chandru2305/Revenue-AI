"""POST /api/v1/demo/seed-batch — the measured batch, triggerable from the UI.

The endpoint uses the real `get_ai_service` dependency. These tests
override it with `scripted_ai_service()` so the outcome distribution is
deterministic in CI — the same stand-in `test_demo_batch.py` uses. One
test deliberately does NOT override it, to check the no-key path.
"""
from __future__ import annotations

import pytest

from app.ai.dependencies import get_ai_service
from app.core.config import get_settings
from app.domain.enums import RecoveryCaseStatus
from app.main import app
from scripts.seed_demo_batch import scripted_ai_service


@pytest.fixture
def _scripted_ai():
    app.dependency_overrides[get_ai_service] = scripted_ai_service
    yield
    app.dependency_overrides.pop(get_ai_service, None)


@pytest.mark.asyncio
async def test_seed_batch_returns_a_measured_summary(client, _scripted_ai):
    response = await client.post("/api/v1/demo/seed-batch")

    assert response.status_code == 200
    body = response.json()
    assert body["cases_processed"] == 30
    assert body["ai_model"] == "scripted-demo-stand-in"
    summary = body["summary"]
    # The headline the Track 03 bar asks for: money recovered across a batch.
    assert summary["confirmed_recovered_revenue"] > 0
    assert summary["eligible_revenue"] > 0
    assert 0 < summary["recovery_rate"] < 1  # a credible mix, not a suspicious 100%


@pytest.mark.asyncio
async def test_batch_demonstrates_escalation_and_stopping_not_just_success(client, _scripted_ai):
    """The bar asks for compliant escalation and stopping rules — a batch
    that recovered everything would prove neither."""
    body = (await client.post("/api/v1/demo/seed-batch")).json()
    counts = body["final_status_counts"]

    assert counts.get(RecoveryCaseStatus.RECOVERED.value, 0) > 0
    assert counts.get(RecoveryCaseStatus.STOPPED.value, 0) > 0
    assert counts.get(RecoveryCaseStatus.ESCALATED.value, 0) > 0
    assert body["summary"]["escalation_rate"] > 0
    assert body["summary"]["stop_rate"] > 0


@pytest.mark.asyncio
async def test_response_states_what_was_live_and_what_was_simulated(client, _scripted_ai):
    """The numbers must not be quotable without knowing the payment
    confirmation was simulated."""
    body = (await client.post("/api/v1/demo/seed-batch")).json()

    provenance = body["provenance"].lower()
    assert "simulated" in provenance
    assert "fakepaymentprovider" in provenance
    assert "razorpay" in provenance
    assert "never quote" in provenance


@pytest.mark.asyncio
async def test_no_ai_key_still_returns_a_batch_that_escalates_safely(client, monkeypatch):
    """No dependency override: the real AIRecommendationService with no
    GROQ_API_KEY falls back to ESCALATE. The batch still completes; it
    just recovers nothing."""
    from app.ai.dependencies import get_ai_provider

    monkeypatch.setattr(get_settings(), "groq_api_key", "")
    get_ai_provider.cache_clear()
    body = (await client.post("/api/v1/demo/seed-batch")).json()
    get_ai_provider.cache_clear()

    assert body["cases_processed"] == 30
    counts = body["final_status_counts"]
    assert counts.get(RecoveryCaseStatus.ESCALATED.value) == 30
    assert body["summary"]["confirmed_recovered_revenue"] == 0


@pytest.mark.asyncio
async def test_seeding_is_refused_in_production(client, monkeypatch, _scripted_ai):
    monkeypatch.setattr(get_settings(), "app_env", "production")

    response = await client.post("/api/v1/demo/seed-batch")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_endpoint_is_behind_the_api_key(client, monkeypatch, _scripted_ai):
    monkeypatch.setattr(get_settings(), "api_key", "rk_test_key")
    assert (await client.post("/api/v1/demo/seed-batch")).status_code == 401
