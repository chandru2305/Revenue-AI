"""Response shape for GET /api/v1/recovery-cases/{id}/timeline.

Directly derived from AuditEvent rows for that case — every field the
frontend timeline shows comes from a real, persisted event. Nothing here
is synthesized or reordered by the API layer beyond chronological sort.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.domain.enums import ActorType


class TimelineEvent(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    event_type: str
    actor_type: ActorType
    payload: dict
    correlation_id: str | None
    created_at: datetime


class TimelineResponse(BaseModel):
    recovery_case_id: uuid.UUID
    events: list[TimelineEvent]
