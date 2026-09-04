"""Application-level concerns: correlation-ID handling and clean shutdown.

Both are things that only misbehave in production (a poisoned log line, a
leaked connection pool), so they're pinned here rather than left to
inspection.
"""
from __future__ import annotations

import pytest

from app.domain.enums import NotificationMedium
from app.domain.providers.base import (
    CreatePaymentLinkRequest,
    PaymentLinkSnapshot,
    PaymentProvider,
    ProviderPaymentSnapshot,
)
from app.main import _CORRELATION_ID_MAX_LENGTH, _safe_correlation_id
from app.payments.dependencies import close_payment_provider, get_payment_provider


class _ClosableProvider(PaymentProvider):
    """Minimal provider that records whether aclose() was called."""

    def __init__(self) -> None:
        self.closed = False

    async def fetch_payment(self, provider_payment_id: str) -> ProviderPaymentSnapshot:
        raise NotImplementedError

    async def create_payment_link(self, request: CreatePaymentLinkRequest) -> PaymentLinkSnapshot:
        raise NotImplementedError

    async def fetch_payment_link(self, provider_reference: str) -> PaymentLinkSnapshot:
        raise NotImplementedError

    async def find_payment_link_by_reference(self, reference_id: str) -> PaymentLinkSnapshot | None:
        raise NotImplementedError

    async def notify_payment_link(self, provider_reference: str, medium: NotificationMedium) -> bool:
        raise NotImplementedError

    async def aclose(self) -> None:
        self.closed = True


# --- correlation ID ---


def test_a_well_formed_correlation_id_is_passed_through():
    assert _safe_correlation_id("abc-123_XY.z:1") == "abc-123_XY.z:1"


@pytest.mark.parametrize(
    "hostile",
    [
        'evil" injected="yes',           # attribute/quote injection
        "line\nbreak",                    # forged second log line
        "tab\tseparated",
        "unicode-‮override",        # RTL override
        "semi;colon",
        "<script>alert(1)</script>",
        "sp ace",
    ],
)
def test_hostile_correlation_ids_are_replaced_not_echoed(hostile):
    result = _safe_correlation_id(hostile)
    assert result != hostile
    assert len(result) == 36  # a generated uuid4


def test_overlong_correlation_id_is_replaced():
    result = _safe_correlation_id("a" * (_CORRELATION_ID_MAX_LENGTH + 1))
    assert len(result) == 36


def test_a_correlation_id_at_the_length_limit_is_accepted():
    at_limit = "a" * _CORRELATION_ID_MAX_LENGTH
    assert _safe_correlation_id(at_limit) == at_limit


def test_missing_or_empty_correlation_id_generates_one():
    for value in (None, ""):
        assert len(_safe_correlation_id(value)) == 36


@pytest.mark.asyncio
async def test_request_echoes_back_a_sanitized_correlation_id(client):
    response = await client.get("/health", headers={"x-correlation-id": "trace\nforged"})
    assert response.status_code == 200
    echoed = response.headers["x-correlation-id"]
    assert "\n" not in echoed
    assert echoed != "trace\nforged"


@pytest.mark.asyncio
async def test_request_preserves_a_valid_correlation_id(client):
    response = await client.get("/health", headers={"x-correlation-id": "req-abc-123"})
    assert response.headers["x-correlation-id"] == "req-abc-123"


# --- provider shutdown ---


@pytest.mark.asyncio
async def test_close_payment_provider_is_a_noop_when_none_was_built():
    """Must not construct a provider just to close it — that would create
    an httpx client during shutdown."""
    get_payment_provider.cache_clear()
    await close_payment_provider()
    assert get_payment_provider.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_close_payment_provider_clears_the_cached_instance():
    get_payment_provider.cache_clear()
    get_payment_provider()  # prime the cache
    assert get_payment_provider.cache_info().currsize == 1

    await close_payment_provider()
    assert get_payment_provider.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_razorpay_provider_aclose_actually_closes_its_http_client():
    """The real leak this fix addresses: a pooled httpx.AsyncClient left
    open when the process shuts down."""
    from app.payments.providers.razorpay import RazorpayPaymentProvider

    provider = RazorpayPaymentProvider(
        key_id="rzp_test_dummy",
        key_secret="dummy_secret",
        base_url="https://api.razorpay.com/v1",
        mode="test",
        timeout_seconds=1.0,
    )
    assert provider._client.is_closed is False
    await provider.aclose()
    assert provider._client.is_closed is True


@pytest.mark.asyncio
async def test_providers_holding_nothing_inherit_a_working_aclose():
    """The shutdown path never has to type-check what it is closing."""
    from app.payments.providers.fake import FakePaymentProvider

    await FakePaymentProvider().aclose()
    await _ClosableProvider().aclose()
