from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.enums import FailureReason, PaymentMethodType, PaymentStatus


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    amount: int
    currency: str
    status: PaymentStatus
    payment_method_type: PaymentMethodType
    failure_reason: FailureReason | None
    attempt_number: int
    provider_payment_id: str | None
    created_at: datetime
    updated_at: datetime
