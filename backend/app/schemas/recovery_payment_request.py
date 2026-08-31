from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.enums import RecoveryPaymentRequestStatus


class RecoveryPaymentRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recovery_attempt_id: uuid.UUID
    provider: str
    provider_reference: str
    short_url: str | None
    amount: int
    amount_paid: int
    currency: str
    status: RecoveryPaymentRequestStatus
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
