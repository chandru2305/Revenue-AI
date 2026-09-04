"""GET /api/v1/system/info — the deployment-wiring descriptor."""
from __future__ import annotations

import pytest

from app.core.config import get_settings


@pytest.mark.asyncio
async def test_reports_demo_mode_when_no_razorpay_key(client):
    """The hermetic fixture blanks the Razorpay keys, so a plain test run
    is always in demo mode — payment execution is simulated."""
    body = (await client.get("/api/v1/system/info")).json()

    assert body["demo_mode"] is True
    assert body["payment_provider"] == "fake"
    assert body["payment_provider_mode"] == "simulated"


@pytest.mark.asyncio
async def test_reports_a_real_gateway_when_razorpay_is_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "razorpay_key_id", "rzp_test_x")
    monkeypatch.setattr(get_settings(), "razorpay_key_secret", "secret_x")

    body = (await client.get("/api/v1/system/info")).json()

    assert body["demo_mode"] is False
    assert body["payment_provider"] == "razorpay"
    assert body["payment_provider_mode"] == "test"


@pytest.mark.asyncio
async def test_surfaces_the_policy_limits_the_engine_enforces(client):
    body = (await client.get("/api/v1/system/info")).json()

    policy = body["policy"]
    assert policy["max_retry_count"] == get_settings().policy_max_retry_count
    assert policy["max_recovery_amount"] == get_settings().policy_max_recovery_amount
    assert policy["min_confidence_threshold"] == get_settings().policy_min_confidence_threshold


@pytest.mark.asyncio
async def test_reports_ai_provider_state(client, monkeypatch):
    assert (await client.get("/api/v1/system/info")).json()["ai_provider"] == "unconfigured"

    monkeypatch.setattr(get_settings(), "groq_api_key", "gsk_test")
    assert (await client.get("/api/v1/system/info")).json()["ai_provider"] == "groq"


@pytest.mark.asyncio
async def test_is_behind_the_api_key(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_key", "rk_test_key")
    assert (await client.get("/api/v1/system/info")).status_code == 401
