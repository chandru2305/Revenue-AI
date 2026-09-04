"""Request/response schemas for the failed-payment ingestion path.

Ingestion is the "left edge" of the recovery workflow: a provider-reported
failed payment becomes a `Payment` row and (optionally, immediately) a
`RecoveryCase` in `DISCOVERED`. Everything downstream — eligibility,
diagnosis, policy, execution — is the existing pipeline, unchanged.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import FailureReason, PaymentMethodType, PaymentStatus, RecoveryCaseStatus


class PaymentIngestRequest(BaseModel):
    """One failed payment as reported by the payment provider.

    `amount` is in the smallest currency unit (paise for INR) — never a
    decimal. `customer_reference` is an opaque handle into the provider's
    customer record; when supplied, repeat ingestions for the same
    reference reuse one `Customer` row so behavioural history accumulates.
    """

    customer_reference: str | None = Field(
        default=None,
        max_length=128,
        description="Opaque provider-side customer id. Omit for an anonymous one-off.",
    )
    amount: int = Field(gt=0, description="Smallest currency unit (e.g. paise).")
    currency: str = Field(default="INR", min_length=3, max_length=3)
    status: PaymentStatus = Field(
        default=PaymentStatus.FAILED,
        description="Only FAILED payments are eligible for a recovery case.",
    )
    payment_method_type: PaymentMethodType = PaymentMethodType.CARD
    failure_reason: FailureReason | None = None
    attempt_number: int = Field(default=1, ge=1)
    provider_payment_id: str | None = Field(default=None, max_length=128)
    auto_create_case: bool = Field(
        default=True,
        description="Create the RecoveryCase immediately (FAILED payments only).",
    )


class PaymentIngestResponse(BaseModel):
    payment_id: uuid.UUID
    customer_id: uuid.UUID
    recovery_case_id: uuid.UUID | None
    recovery_case_status: RecoveryCaseStatus | None
    correlation_id: str
    deduplicated: bool = Field(
        default=False,
        description="True when this call matched an existing payment by "
        "`provider_payment_id` and returned it instead of creating a duplicate.",
    )


class RecoveryCaseCreateRequest(BaseModel):
    payment_id: uuid.UUID


class RecoveryCaseCreatedResponse(BaseModel):
    recovery_case_id: uuid.UUID
    payment_id: uuid.UUID
    status: RecoveryCaseStatus
    revenue_at_risk: int
    created: bool = Field(
        description="True if this call created the case; False if one already existed."
    )
    correlation_id: str


class DiscoveryReport(BaseModel):
    """Result of a discovery sweep over failed payments without a case."""

    scanned: int
    created: int
    skipped_existing: int
    case_ids: list[uuid.UUID]
    generated_at: datetime
    correlation_id: str
