from __future__ import annotations

from app.models.processed_webhook_event import ProcessedWebhookEvent
from app.repositories.base import BaseRepository


class ProcessedWebhookEventRepository(BaseRepository[ProcessedWebhookEvent]):
    model = ProcessedWebhookEvent
