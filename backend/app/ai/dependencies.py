"""FastAPI dependency wiring for the AI layer.

Kept separate from `providers/gemini.py` so provider construction failures
(most commonly: no `GEMINI_API_KEY` configured) are handled as a normal,
gracefully-degraded provider — the diagnose endpoint falls back to
ESCALATE exactly like any other AI-unavailable scenario, rather than the
app crashing or every request 500ing because of a missing env var.
"""
from __future__ import annotations

from functools import lru_cache

from app.ai.context import PaymentRecoveryContext
from app.ai.providers.base import AIProvider, AIProviderAuthError
from app.ai.providers.gemini import GeminiProvider
from app.ai.schemas import RecoveryRecommendation
from app.ai.service import AIRecommendationService
from app.core.config import get_settings


class _UnconfiguredProvider(AIProvider):
    """Stands in for Gemini when no API key is configured. Always fails
    with an auth error, which the AIRecommendationService already knows
    how to turn into a safe ESCALATE fallback."""

    async def diagnose_payment(self, context: PaymentRecoveryContext) -> RecoveryRecommendation:
        raise AIProviderAuthError("GEMINI_API_KEY is not configured.")


@lru_cache
def get_ai_provider() -> AIProvider:
    settings = get_settings()
    if not settings.gemini_api_key:
        return _UnconfiguredProvider()
    return GeminiProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.ai_request_timeout_seconds,
    )


def get_ai_service() -> AIRecommendationService:
    settings = get_settings()
    return AIRecommendationService(
        get_ai_provider(), model_name=settings.gemini_model, max_retries=settings.ai_max_retries
    )
