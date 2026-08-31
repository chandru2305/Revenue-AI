from __future__ import annotations

from pydantic import BaseModel


class WebhookAckResponse(BaseModel):
    """Always HTTP 200 with one of these statuses — Razorpay retries a
    webhook delivery on anything else, and we never want a retry storm
    for an event we've already (or can never) process. Signature failures
    are the one case that returns a non-200 (401) — see
    app.api.v1.webhooks."""

    status: str  # "processed" | "duplicate" | "ignored"
    event_type: str | None = None
