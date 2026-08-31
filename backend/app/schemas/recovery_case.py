from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.enums import DiagnosisCategory, RecoveryAction, RecoveryCaseStatus
from app.schemas.payment import PaymentRead
from app.schemas.recovery_attempt import RecoveryAttemptRead
from app.schemas.recovery_payment_request import RecoveryPaymentRequestRead


class RecoveryCaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payment_id: uuid.UUID
    status: RecoveryCaseStatus
    revenue_at_risk: int
    recovered_amount: int
    eligible: bool
    diagnosis_category: DiagnosisCategory | None
    recovery_confidence: float | None
    recommended_action: RecoveryAction | None
    current_attempt_number: int
    customer_contact_count: int
    policy_version: str | None
    created_at: datetime
    updated_at: datetime


class RecoveryCaseDetail(RecoveryCaseRead):
    diagnosis_notes: str | None
    attempts: list[RecoveryAttemptRead] = []
    payment_requests: list[RecoveryPaymentRequestRead] = []
    payment: PaymentRead
