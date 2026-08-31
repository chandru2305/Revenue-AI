"""Razorpay payment provider, via the documented REST API (Basic Auth,
`https://api.razorpay.com/v1`).

Verified against current official Razorpay documentation during Phase 3
development (see docs/razorpay-integration.md for exactly what was
checked): Payment Links (`POST/GET /payment_links`,
`POST /payment_links/:id/notify_by/:medium`), webhook signature
verification, and `GET /payments/:id`. No endpoint here was invented.

Test Mode guard: `__init__` refuses to construct unless
`settings.razorpay_mode == "test"` — see `_require_test_mode`. This is
deliberately not bypassable via a constructor argument.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger, log_event
from app.domain.enums import NotificationMedium, PaymentStatus, RecoveryPaymentRequestStatus
from app.domain.providers.base import (
    CreatePaymentLinkRequest,
    PaymentLinkSnapshot,
    PaymentProvider,
    PaymentProviderAmbiguousError,
    PaymentProviderAuthError,
    PaymentProviderError,
    PaymentProviderRateLimitError,
    PaymentProviderTimeoutError,
    PaymentProviderUnavailableError,
    PaymentProviderValidationError,
    ProviderPaymentSnapshot,
)

logger = get_logger(__name__)

PROVIDER_NAME = "razorpay"


class RazorpayModeError(Exception):
    """Raised at construction time if RAZORPAY_MODE is not 'test'. Never
    caught and converted into a fallback — a misconfigured live-mode
    guard must fail loudly, not degrade gracefully."""


def _require_test_mode(mode: str) -> None:
    if mode != "test":
        raise RazorpayModeError(
            f"RazorpayPaymentProvider refuses to run with RAZORPAY_MODE={mode!r}. "
            "Phase 3 only ever runs against Razorpay Test Mode; this guard is "
            "intentionally not bypassable — see docs/razorpay-integration.md."
        )


def _to_unix(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp())


def _from_unix(value: int | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(value, tz=UTC)


def _map_payment_link_status(status: str) -> RecoveryPaymentRequestStatus:
    try:
        return RecoveryPaymentRequestStatus(status)
    except ValueError:
        # Unrecognized status from a future API change — fail closed by
        # treating it as still "created" (i.e. not yet paid), never
        # silently mapped to "paid".
        return RecoveryPaymentRequestStatus.CREATED


def _payment_link_from_response(data: dict[str, Any]) -> PaymentLinkSnapshot:
    return PaymentLinkSnapshot(
        provider_reference=data["id"],
        short_url=data.get("short_url"),
        status=_map_payment_link_status(data["status"]),
        amount=data["amount"],
        amount_paid=data.get("amount_paid", 0),
        currency=data["currency"],
        reference_id=data.get("reference_id"),
        expires_at=_from_unix(data.get("expire_by")),
    )


class RazorpayPaymentProvider(PaymentProvider):
    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        base_url: str,
        mode: str,
        timeout_seconds: float,
    ) -> None:
        _require_test_mode(mode)
        if not key_id or not key_secret:
            raise ValueError("RazorpayPaymentProvider requires both a key_id and a key_secret.")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            auth=httpx.BasicAuth(key_id, key_secret),
            timeout=timeout_seconds,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- Reads (safe to classify as ordinary timeout/unavailable) ---

    async def fetch_payment(self, provider_payment_id: str) -> ProviderPaymentSnapshot:
        data = await self._request("GET", f"/payments/{provider_payment_id}")
        return ProviderPaymentSnapshot(
            provider_payment_id=data["id"],
            status=PaymentStatus(data["status"]),
            amount=data["amount"],
            currency=data["currency"],
            method=data.get("method"),
        )

    async def fetch_payment_link(self, provider_reference: str) -> PaymentLinkSnapshot:
        data = await self._request("GET", f"/payment_links/{provider_reference}")
        return _payment_link_from_response(data)

    async def find_payment_link_by_reference(self, reference_id: str) -> PaymentLinkSnapshot | None:
        data = await self._request("GET", "/payment_links/", params={"reference_id": reference_id})
        links = data.get("payment_links", [])
        if not links:
            return None
        return _payment_link_from_response(links[0])

    # --- Writes ---

    async def create_payment_link(self, request: CreatePaymentLinkRequest) -> PaymentLinkSnapshot:
        """NOT retried on ambiguous failure — see PaymentProviderAmbiguousError
        on the interface. Callers must reconcile via
        `find_payment_link_by_reference` instead of calling this again."""
        body: dict[str, Any] = {
            "amount": request.amount,
            "currency": request.currency,
            "reference_id": request.reference_id,
            "description": request.description,
            "accept_partial": False,
        }
        if request.expire_by is not None:
            body["expire_by"] = _to_unix(request.expire_by)

        try:
            data = await self._request("POST", "/payment_links/", json=body)
        except (PaymentProviderTimeoutError, PaymentProviderUnavailableError) as exc:
            raise PaymentProviderAmbiguousError(
                f"Payment link creation result is uncertain: {exc}"
            ) from exc
        return _payment_link_from_response(data)

    async def notify_payment_link(self, provider_reference: str, medium: NotificationMedium) -> bool:
        await self._request("POST", f"/payment_links/{provider_reference}/notify_by/{medium.value}")
        return True

    # --- HTTP plumbing ---

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = await self._client.request(method, f"{self._base_url}{path}", json=json, params=params)
        except httpx.TimeoutException as exc:
            self._log_call(method, path, started, status_code=None, error="timeout")
            raise PaymentProviderTimeoutError(f"Razorpay request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            self._log_call(method, path, started, status_code=None, error=type(exc).__name__)
            raise PaymentProviderUnavailableError(f"Razorpay request failed: {exc}") from exc

        self._log_call(method, path, started, status_code=response.status_code)

        if response.status_code < 300:
            result: dict[str, Any] = response.json()
            return result

        raise self._classify_error_response(response)

    def _classify_error_response(self, response: httpx.Response) -> PaymentProviderError:
        try:
            body = response.json()
            description = body.get("error", {}).get("description", "")
        except ValueError:
            description = response.text[:200]

        status_code = response.status_code
        message = f"Razorpay returned {status_code}: {description}"

        if status_code in (401, 403):
            return PaymentProviderAuthError(message)
        if status_code == 429:
            return PaymentProviderRateLimitError(message)
        if status_code in (400, 422):
            return PaymentProviderValidationError(message)
        # 5xx and anything unrecognized: we don't know if the request was
        # actually processed before the server-side failure.
        return PaymentProviderUnavailableError(message)

    def _log_call(
        self, method: str, path: str, started: float, *, status_code: int | None, error: str | None = None
    ) -> None:
        latency_ms = (time.perf_counter() - started) * 1000
        log_event(
            logger,
            logging.INFO if error is None else logging.WARNING,
            "razorpay_api_call",
            method=method,
            path=path,
            status_code=status_code,
            latency_ms=round(latency_ms, 1),
            error=error,
        )
