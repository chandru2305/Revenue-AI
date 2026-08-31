"""Orchestrates a provider call with retry + safe fallback.

This module contains NO policy logic — it never decides whether a
recommendation is *permitted*, only whether one could be *produced*. The
deterministic policy engine (`app.domain.policy`) always runs afterward,
on every recommendation, AI-sourced or fallback-sourced alike. See
docs/ai-safety.md for why the fallback path still goes through policy
rather than being special-cased.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.ai.context import PaymentRecoveryContext
from app.ai.prompts.diagnosis_v1 import PROMPT_VERSION
from app.ai.providers.base import (
    AIProvider,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from app.ai.schemas import RecoveryRecommendation
from app.core.logging import get_logger, log_event
from app.domain.enums import DecisionSource, DiagnosisCategory, RecoveryAction

logger = get_logger(__name__)

# Only retry failure modes that are plausibly transient. Auth failures and
# invalid-response/schema failures will not be fixed by trying again.
_RETRYABLE_ERROR_TYPES = (AIProviderTimeoutError, AIProviderRateLimitError, AIProviderUnavailableError)


@dataclass(frozen=True)
class AIOutcome:
    decision_source: DecisionSource
    recommendation: RecoveryRecommendation
    model: str | None
    prompt_version: str
    latency_ms: float
    retry_count: int
    failure_code: str | None = None
    failure_message: str | None = None


def _fallback_recommendation(error: AIProviderError | None) -> RecoveryRecommendation:
    reason = str(error) if error is not None else "AI provider unavailable"
    return RecoveryRecommendation(
        diagnosis_category=DiagnosisCategory.UNKNOWN_FAILURE,
        recovery_confidence=0.0,
        recommended_action=RecoveryAction.ESCALATE,
        decision_explanation=f"AI diagnosis unavailable; escalating to a human reviewer. ({reason[:200]})",
    )


class AIRecommendationService:
    def __init__(self, provider: AIProvider, *, model_name: str, max_retries: int = 1) -> None:
        self._provider = provider
        self._model_name = model_name
        self._max_retries = max(0, max_retries)

    async def get_recommendation(self, context: PaymentRecoveryContext) -> AIOutcome:
        """Never raises. Always returns a usable AIOutcome — AI-sourced on
        success, fallback-sourced (recommending ESCALATE) on any failure."""
        started = time.perf_counter()
        last_error: AIProviderError | None = None
        attempts = 0

        while attempts <= self._max_retries:
            attempts += 1
            try:
                recommendation = await self._provider.diagnose_payment(context)
            except AIProviderError as exc:
                last_error = exc
                log_event(
                    logger,
                    logging.WARNING,
                    "ai_provider_call_failed",
                    attempt=attempts,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    retryable=isinstance(exc, _RETRYABLE_ERROR_TYPES),
                )
                if isinstance(exc, _RETRYABLE_ERROR_TYPES) and attempts <= self._max_retries:
                    continue
                break
            else:
                latency_ms = (time.perf_counter() - started) * 1000
                return AIOutcome(
                    decision_source=DecisionSource.AI,
                    recommendation=recommendation,
                    model=self._model_name,
                    prompt_version=PROMPT_VERSION,
                    latency_ms=latency_ms,
                    retry_count=attempts - 1,
                )

        latency_ms = (time.perf_counter() - started) * 1000
        log_event(
            logger,
            logging.ERROR,
            "ai_diagnosis_fallback_triggered",
            attempts=attempts,
            error_type=type(last_error).__name__ if last_error else None,
        )
        return AIOutcome(
            decision_source=DecisionSource.FALLBACK,
            recommendation=_fallback_recommendation(last_error),
            model=None,
            prompt_version=PROMPT_VERSION,
            latency_ms=latency_ms,
            retry_count=attempts - 1,
            failure_code=type(last_error).__name__ if last_error else "unknown_error",
            failure_message=str(last_error) if last_error else None,
        )
