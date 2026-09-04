"""Payment provider abstraction.

Phase 1 defined a placeholder contract shaped around a generic "retry
action." Phase 3 replaces it with the real shape once actual Razorpay
behavior was verified against current official documentation: Razorpay's
Payments API is for retrieving payments and moving `authorized` ->
`captured` — it is NOT a generic "retry this failed payment" endpoint.
The initial real recovery action is therefore a **Payment Link**: create
one, optionally notify the customer, and observe whether it gets paid.
See docs/razorpay-integration.md for exactly which endpoints this maps to
and what was verified.

The domain layer depends only on this interface, never on a concrete
provider. `app.payments.providers.razorpay.RazorpayPaymentProvider` and
`app.payments.providers.fake.FakePaymentProvider` both implement it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import (
    NotificationMedium,
    PaymentStatus,
    RecoveryAction,
    RecoveryPaymentRequestStatus,
)

# Which recovery actions this provider abstraction can actually carry out.
#
# Deliberately narrower than `RecoveryAction`: RETRY_PAYMENT has no
# real-provider implementation (Razorpay has no generic "retry a failed
# payment" endpoint — see docs/razorpay-integration.md "Reality check"),
# and SEND_REMINDER's notify path exists on the interface but isn't wired
# into the execution flow yet.
#
# This lives in the domain layer rather than inside execution_service so
# that diagnosis_service can consult it too: a case whose recommended
# action can't be executed must never reach APPROVED, because APPROVED
# means "ready to execute". Without this, such a case advertised an
# Execute button that could only ever fail.
EXECUTABLE_ACTIONS: frozenset[RecoveryAction] = frozenset({RecoveryAction.SEND_PAYMENT_LINK})


class PaymentProviderError(Exception):
    """Base class for provider failures. A provider never raises a raw
    SDK/HTTP exception past this boundary — see each provider's own
    `_classify`."""


class PaymentProviderTimeoutError(PaymentProviderError):
    pass


class PaymentProviderAuthError(PaymentProviderError):
    pass


class PaymentProviderRateLimitError(PaymentProviderError):
    pass


class PaymentProviderValidationError(PaymentProviderError):
    """The request itself was rejected (bad amount, bad params, ...). Not
    retryable — retrying an invalid request just fails the same way."""


class PaymentProviderAmbiguousError(PaymentProviderError):
    """The request may or may not have succeeded (e.g. a timeout after the
    request was sent). Callers must reconcile via
    `find_payment_link_by_reference` before ever creating a second
    resource for the same recovery attempt — see
    docs/razorpay-integration.md "Ambiguous result handling"."""


class PaymentProviderUnavailableError(PaymentProviderError):
    """Any other transient provider-side failure (network, 5xx, ...)."""


@dataclass(frozen=True)
class ProviderPaymentSnapshot:
    provider_payment_id: str
    status: PaymentStatus
    amount: int
    currency: str
    method: str | None = None


@dataclass(frozen=True)
class CreatePaymentLinkRequest:
    """Everything needed to create one payment link. `reference_id` is our
    own correlator (set to the RecoveryAttempt id) — Razorpay's fetch-all
    API supports filtering by it, which is exactly what reconciliation
    after an ambiguous result relies on."""

    reference_id: str
    amount: int
    currency: str
    description: str
    expire_by: datetime | None = None


@dataclass(frozen=True)
class PaymentLinkSnapshot:
    provider_reference: str
    short_url: str | None
    status: RecoveryPaymentRequestStatus
    amount: int
    amount_paid: int
    currency: str
    reference_id: str | None
    expires_at: datetime | None


class PaymentProvider(ABC):
    """Boundary between RecoverAI and an external payment gateway.

    `create_payment_link` is NOT guaranteed idempotent by the provider —
    Razorpay's Payment Links API does not document a client-supplied
    idempotency key. Callers must not call it twice for the same
    RecoveryAttempt; on an ambiguous outcome, reconcile via
    `find_payment_link_by_reference` before ever retrying.
    """

    @abstractmethod
    async def fetch_payment(self, provider_payment_id: str) -> ProviderPaymentSnapshot:
        raise NotImplementedError

    @abstractmethod
    async def create_payment_link(self, request: CreatePaymentLinkRequest) -> PaymentLinkSnapshot:
        raise NotImplementedError

    @abstractmethod
    async def fetch_payment_link(self, provider_reference: str) -> PaymentLinkSnapshot:
        raise NotImplementedError

    @abstractmethod
    async def find_payment_link_by_reference(self, reference_id: str) -> PaymentLinkSnapshot | None:
        """Returns None if no payment link with this reference_id exists
        yet — used to reconcile after a PaymentProviderAmbiguousError."""
        raise NotImplementedError

    @abstractmethod
    async def notify_payment_link(self, provider_reference: str, medium: NotificationMedium) -> bool:
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release any long-lived resources (a pooled HTTP client, a socket).

        Deliberately concrete-with-a-no-op rather than abstract: a provider
        holding nothing (the fake, the unconfigured stand-in) shouldn't be
        forced to write an empty method, and the application shutdown path
        can call this on whatever provider is active without type-checking
        it first. See `app.main.lifespan`.
        """
        return None
