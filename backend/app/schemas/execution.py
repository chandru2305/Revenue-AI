from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.domain.enums import (
    PolicyDecisionType,
    PolicyReasonCode,
    RecoveryCaseStatus,
    RecoveryPaymentRequestStatus,
)


class ExecutionResponse(BaseModel):
    """Response for POST /api/v1/recovery-cases/{id}/execute.

    `executed` is false whenever nothing was sent to Razorpay — blocked by
    the pre-execution re-check, an already-active payment request, or an
    unsupported action. When `executed` is true but `payment_link_status`
    is not yet "paid", the case is `EXECUTING` and awaiting the customer;
    see docs/razorpay-integration.md "Recovered means confirmed paid."
    """

    recovery_case_id: uuid.UUID
    case_status: RecoveryCaseStatus
    correlation_id: str

    executed: bool
    reason: str | None = None

    policy_decision: PolicyDecisionType | None = None
    policy_reason_codes: list[PolicyReasonCode] = []

    provider_reference: str | None = None
    short_url: str | None = None
    payment_link_status: RecoveryPaymentRequestStatus | None = None
    amount: int | None = None
    currency: str | None = None
    expires_at: datetime | None = None
