"""API-key authentication.

Covers the three postures in app/core/auth.py — unset (open, dev),
set (enforced), and unset-in-production (refuses to start) — plus the two
endpoints deliberately exempt from the key.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.core.auth import InsecureConfigurationError, validate_auth_configuration
from app.core.config import Settings, get_settings

API_KEY = "rk_test_a_shared_deployment_key"


@pytest.fixture
def with_api_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_key", API_KEY)
    return API_KEY


@pytest.fixture
def without_api_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_key", "")


# --- enforcement ---


@pytest.mark.asyncio
async def test_request_with_the_correct_key_is_allowed(client, with_api_key):
    response = await client.get("/api/v1/payments", headers={"x-api-key": with_api_key})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_request_without_a_key_is_rejected(client, with_api_key):
    response = await client.get("/api/v1/payments")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_request_with_a_wrong_key_is_rejected(client, with_api_key):
    response = await client.get("/api/v1/payments", headers={"x-api-key": "wrong"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_and_wrong_keys_are_indistinguishable(client, with_api_key):
    """A caller without the key must not learn whether it sent nothing or
    sent something incorrect."""
    missing = await client.get("/api/v1/payments")
    wrong = await client.get("/api/v1/payments", headers={"x-api-key": "wrong"})
    assert missing.status_code == wrong.status_code
    assert missing.json() == wrong.json()


@pytest.mark.asyncio
async def test_a_near_miss_key_is_rejected(client, with_api_key):
    for variant in (with_api_key[:-1], with_api_key + "x", with_api_key.upper(), f" {with_api_key}"):
        response = await client.get("/api/v1/payments", headers={"x-api-key": variant})
        assert response.status_code == 401, f"accepted a near-miss key: {variant!r}"


@pytest.mark.asyncio
async def test_write_endpoints_are_protected_too(client, with_api_key):
    """Auth must cover the endpoints that change state, not just reads."""
    response = await client.post("/api/v1/payments", json={"amount": 1000})
    assert response.status_code == 401

    response = await client.post(f"/api/v1/recovery-cases/{uuid.uuid4()}/diagnose")
    assert response.status_code == 401

    response = await client.post("/api/v1/recovery-cases/discover")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_malformed_body_can_return_422_before_401(client, with_api_key):
    """Documents a known, accepted ordering rather than asserting it is
    ideal: FastAPI validates the request body in the same pass that
    resolves router-level dependencies, so a syntactically invalid body
    can surface a 422 before the API-key check rejects the caller.

    Accepted because it grants nothing — no handler runs, no state
    changes — and the schema it hints at is already public via the
    unauthenticated /docs and /openapi.json. Enforcing auth strictly
    first would mean moving it into middleware and re-implementing the
    /health and /webhooks exemptions as path matching, which is far more
    fragile than the declarative router-level dependency.

    A *well-formed* body must still be rejected with 401 — that is the
    case that matters, and the assertion below pins it.
    """
    malformed = await client.post(
        "/api/v1/payments", content=b"not-json", headers={"Content-Type": "application/json"}
    )
    assert malformed.status_code in (401, 422)

    well_formed = await client.post("/api/v1/payments", json={"amount": 100})
    assert well_formed.status_code == 401


@pytest.mark.asyncio
async def test_every_v1_resource_router_is_behind_the_key(client, with_api_key):
    for path in (
        "/api/v1/payments",
        "/api/v1/recovery-cases",
        "/api/v1/audit-events",
        "/api/v1/evaluation/summary",
        "/api/v1/evaluation/recovery-summary",
    ):
        assert (await client.get(path)).status_code == 401, f"{path} is not protected"


# --- deliberate exemptions ---


@pytest.mark.asyncio
async def test_health_never_requires_a_key(client, with_api_key):
    """A load balancer probe shouldn't need credentials."""
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_webhook_does_not_require_the_api_key(client, with_api_key, monkeypatch):
    """Razorpay cannot send our key — the webhook authenticates by HMAC
    signature instead. A *wrongly signed* request must still be rejected,
    proving the endpoint is exempt from the key but not unprotected."""
    monkeypatch.setattr(get_settings(), "razorpay_webhook_secret", "whsec_x")
    body = json.dumps({"event": "payment_link.paid"}).encode()

    response = await client.post(
        "/api/v1/webhooks/razorpay", content=body, headers={"x-razorpay-signature": "0" * 64}
    )

    # 401 from signature verification, not from the missing API key — the
    # distinction is what this test pins.
    assert response.status_code == 401
    assert response.json()["status"] == "rejected"


# --- unset key (development default) ---


@pytest.mark.asyncio
async def test_no_key_configured_leaves_the_api_open(client, without_api_key):
    response = await client.get("/api/v1/payments")
    assert response.status_code == 200


# --- startup configuration guard ---


def test_production_without_a_key_refuses_to_start():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:", app_env="production", api_key=""
    )
    with pytest.raises(InsecureConfigurationError):
        validate_auth_configuration(settings)


def test_production_with_a_key_starts():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:", app_env="production", api_key=API_KEY
    )
    validate_auth_configuration(settings)  # must not raise


@pytest.mark.parametrize("env", ["development", "test"])
def test_non_production_without_a_key_only_warns(env):
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", app_env=env, api_key="")
    validate_auth_configuration(settings)  # must not raise
