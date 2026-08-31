"""POST /api/v1/webhooks/razorpay — the only path by which a case can ever
reach RECOVERED. See docs/razorpay-integration.md "Webhook security."

Reads the RAW request body (not FastAPI's parsed JSON) because signature
verification is over the exact bytes Razorpay signed — re-serializing
parsed JSON can silently change byte-for-byte content (key order,
whitespace) and break verification.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_correlation_id, get_logger, log_event
from app.db.session import get_db
from app.payments.webhooks import parse_event, verify_signature
from app.schemas.webhook import WebhookAckResponse
from app.services import webhook_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

logger = get_logger(__name__)


@router.post("/razorpay", response_model=WebhookAckResponse)
async def receive_razorpay_webhook(
    request: Request, response: Response, session: AsyncSession = Depends(get_db)
) -> WebhookAckResponse:
    settings = get_settings()
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    if not verify_signature(raw_body, signature, settings.razorpay_webhook_secret):
        log_event(logger, logging.WARNING, "webhook_signature_verification_failed")
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return WebhookAckResponse(status="rejected")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return WebhookAckResponse(status="rejected")

    event = parse_event(payload)
    correlation_id = get_correlation_id() or event.dedup_key

    result = await webhook_service.process_webhook_event(session, event, correlation_id=correlation_id)
    return WebhookAckResponse(status=result.status, event_type=result.event_type)
