"""FastAPI dependency wiring for the AI layer.

Kept separate from the concrete provider so a construction failure (most
commonly: `GROQ_API_KEY` isn't configured) is handled as a normal,
gracefully-degraded provider — the diagnose endpoint falls back to
ESCALATE exactly like any other AI-unavailable scenario, rather than the
app crashing or every request 500ing on a missing env var.

Groq is the only real provider. The `AIProvider` interface remains the
seam it always was: `FakeAIProvider` implements it for every test, and
swapping in another vendor means adding one file in `providers/` and
changing this function — nothing in the services or API layer moves.
"""
from __future__ import annotations

from functools import lru_cache

from app.ai.context import PaymentRecoveryContext
from app.ai.providers.base import AIProvider, AIProviderAuthError
from app.ai.providers.groq import GroqProvider
from app.ai.schemas import RecoveryRecommendation
from app.ai.service import AIRecommendationService
from app.core.config import get_settings


class _UnconfiguredProvider(AIProvider):
    """Stands in when no API key is configured. Always fails with an auth
    error, which AIRecommendationService already turns into a safe
    ESCALATE fallback."""

    async def diagnose_payment(self, context: PaymentRecoveryContext) -> RecoveryRecommendation:
        raise AIProviderAuthError("GROQ_API_KEY is not configured.")


@lru_cache
def get_ai_provider() -> AIProvider:
    settings = get_settings()
    if not settings.groq_api_key:
        return _UnconfiguredProvider()
    return GroqProvider(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        timeout_seconds=settings.ai_request_timeout_seconds,
    )


def get_ai_service() -> AIRecommendationService:
    settings = get_settings()
    return AIRecommendationService(
        get_ai_provider(), model_name=settings.groq_model, max_retries=settings.ai_max_retries
    )
