"""Deterministic fake payment provider for tests and local development
without Razorpay credentials.

Never calls a real API; never presents its output as if it came from
Razorpay. Configure the behavior you need for a given test, exactly like
`app.ai.providers.fake.FakeAIProvider`.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.enums import NotificationMedium, PaymentStatus, RecoveryPaymentRequestStatus
from app.domain.providers.base import (
    CreatePaymentLinkRequest,
    PaymentLinkSnapshot,
    PaymentProvider,
    PaymentProviderError,
    ProviderPaymentSnapshot,
)


class FakePaymentProvider(PaymentProvider):
    """Behaviors:
      - "success" (default): create_payment_link succeeds normally.
      - "ambiguous": create_payment_link raises the given error (typically
        PaymentProviderAmbiguousError); a subsequent
        find_payment_link_by_reference call returns `reconciled_link` if
        set, simulating "it actually did get created."
      - "failure": create_payment_link raises the given error and stays
        failed on reconciliation too (`reconciled_link=None`).
    """

    def __init__(
        self,
        *,
        create_error: PaymentProviderError | None = None,
        reconciled_link: PaymentLinkSnapshot | None = None,
        notify_result: bool = True,
        notify_error: PaymentProviderError | None = None,
    ) -> None:
        self._create_error = create_error
        self._reconciled_link = reconciled_link
        self._notify_result = notify_result
        self._notify_error = notify_error
        self.created_links: list[PaymentLinkSnapshot] = []
        self.create_call_count = 0
        self.notify_call_count = 0
        self.find_by_reference_call_count = 0

    async def fetch_payment(self, provider_payment_id: str) -> ProviderPaymentSnapshot:
        return ProviderPaymentSnapshot(
            provider_payment_id=provider_payment_id,
            status=PaymentStatus.CAPTURED,
            amount=0,
            currency="INR",
        )

    async def create_payment_link(self, request: CreatePaymentLinkRequest) -> PaymentLinkSnapshot:
        self.create_call_count += 1
        if self._create_error is not None:
            raise self._create_error
        snapshot = PaymentLinkSnapshot(
            provider_reference=f"plink_fake_{uuid.uuid4().hex[:14]}",
            short_url=f"https://rzp.io/i/fake{uuid.uuid4().hex[:8]}",
            status=RecoveryPaymentRequestStatus.CREATED,
            amount=request.amount,
            amount_paid=0,
            currency=request.currency,
            reference_id=request.reference_id,
            expires_at=request.expire_by or (datetime.now(UTC) + timedelta(hours=72)),
        )
        self.created_links.append(snapshot)
        return snapshot

    async def fetch_payment_link(self, provider_reference: str) -> PaymentLinkSnapshot:
        for link in self.created_links:
            if link.provider_reference == provider_reference:
                return link
        raise PaymentProviderError(f"Fake provider has no link {provider_reference!r}.")

    async def find_payment_link_by_reference(self, reference_id: str) -> PaymentLinkSnapshot | None:
        self.find_by_reference_call_count += 1
        for link in self.created_links:
            if link.reference_id == reference_id:
                return link
        # `reference_id` is generated fresh inside execution_service on
        # every call (a uuid4), so a test configuring `reconciled_link`
        # can't predict it in advance — returning it regardless of the
        # exact value is the fake's whole point: "reconciliation finds
        # this link," independent of what reference was asked about.
        return self._reconciled_link

    async def notify_payment_link(self, provider_reference: str, medium: NotificationMedium) -> bool:
        self.notify_call_count += 1
        if self._notify_error is not None:
            raise self._notify_error
        return self._notify_result
