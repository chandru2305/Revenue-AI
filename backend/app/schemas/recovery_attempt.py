from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.enums import RecoveryAction, RecoveryAttemptStatus


class RecoveryAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recovery_case_id: uuid.UUID
    action: RecoveryAction
    status: RecoveryAttemptStatus
    provider: str | None
    amount: int | None
    currency: str | None
    reason: str | None
    provider_reference: str | None
    idempotency_key: str | None
    correlation_id: str | None
    failure_code: str | None
    failure_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
