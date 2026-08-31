"""FastAPI dependency wiring for the payments layer.

Mirrors `app.ai.dependencies`: a missing key doesn't crash the app or fail
every request with a 500 — it resolves to a stand-in provider that always
raises `PaymentProviderAuthError`, which `execution_service` already
handles as a normal provider failure. A misconfigured `RAZORPAY_MODE`
(not "test"), by contrast, is deliberately NOT softened this way — see
`app.payments.providers.razorpay._require_test_mode`.
"""
from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.domain.enums import NotificationMedium
from app.domain.providers.base import (
    CreatePaymentLinkRequest,
    PaymentLinkSnapshot,
    PaymentProvider,
    PaymentProviderAuthError,
    ProviderPaymentSnapshot,
)
from app.payments.providers.razorpay import RazorpayPaymentProvider

_NOT_CONFIGURED_MESSAGE = "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not configured."


class _UnconfiguredPaymentProvider(PaymentProvider):
    async def fetch_payment(self, provider_payment_id: str) -> ProviderPaymentSnapshot:
        raise PaymentProviderAuthError(_NOT_CONFIGURED_MESSAGE)

    async def create_payment_link(self, request: CreatePaymentLinkRequest) -> PaymentLinkSnapshot:
        raise PaymentProviderAuthError(_NOT_CONFIGURED_MESSAGE)

    async def fetch_payment_link(self, provider_reference: str) -> PaymentLinkSnapshot:
        raise PaymentProviderAuthError(_NOT_CONFIGURED_MESSAGE)

    async def find_payment_link_by_reference(self, reference_id: str) -> PaymentLinkSnapshot | None:
        raise PaymentProviderAuthError(_NOT_CONFIGURED_MESSAGE)

    async def notify_payment_link(self, provider_reference: str, medium: NotificationMedium) -> bool:
        raise PaymentProviderAuthError(_NOT_CONFIGURED_MESSAGE)


@lru_cache
def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        return _UnconfiguredPaymentProvider()
    return RazorpayPaymentProvider(
        key_id=settings.razorpay_key_id,
        key_secret=settings.razorpay_key_secret,
        base_url=settings.razorpay_base_url,
        mode=settings.razorpay_mode,
        timeout_seconds=settings.razorpay_request_timeout_seconds,
    )
