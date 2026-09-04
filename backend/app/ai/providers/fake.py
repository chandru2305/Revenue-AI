"""Deterministic fake provider for tests and local development without a
Groq API key.

`FakeAIProvider` never calls a real API and produces the exact behavior
requested — never labeled as if it were a real model's output. Tests must
be able to exercise every failure path (`app.services.diagnosis_service`)
without depending on Groq being reachable or credentials being present.
"""
from __future__ import annotations

from collections.abc import Callable

from app.ai.context import PaymentRecoveryContext
from app.ai.providers.base import (
    AIProvider,
    AIProviderAuthError,
    AIProviderError,
    AIProviderInvalidResponseError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from app.ai.schemas import RecoveryRecommendation


class FakeAIProvider(AIProvider):
    """Configure with one behavior, then use it exactly like a real provider.

    Behaviors:
      - recommendation=<RecoveryRecommendation>  -> returns it verbatim
      - recommend_fn=<ctx -> RecoveryRecommendation>  -> returns per-case output
        (a deterministic stand-in for a real model across a varied batch)
      - raise_error=<AIProviderError instance>    -> raises it
      - malformed=True                            -> raises AIProviderInvalidResponseError
    """

    def __init__(
        self,
        *,
        recommendation: RecoveryRecommendation | None = None,
        recommend_fn: Callable[[PaymentRecoveryContext], RecoveryRecommendation] | None = None,
        raise_error: AIProviderError | None = None,
        malformed: bool = False,
    ) -> None:
        chosen = [recommendation is not None, recommend_fn is not None, raise_error is not None, malformed]
        if sum(chosen) != 1:
            raise ValueError(
                "FakeAIProvider requires exactly one of "
                "recommendation/recommend_fn/raise_error/malformed."
            )
        self._recommendation = recommendation
        self._recommend_fn = recommend_fn
        self._raise_error = raise_error
        self._malformed = malformed
        self.call_count = 0

    async def diagnose_payment(self, context: PaymentRecoveryContext) -> RecoveryRecommendation:
        self.call_count += 1
        if self._malformed:
            raise AIProviderInvalidResponseError("Fake malformed provider output for testing.")
        if self._raise_error is not None:
            raise self._raise_error
        if self._recommend_fn is not None:
            return self._recommend_fn(context)
        assert self._recommendation is not None  # guaranteed by __init__ validation
        return self._recommendation


# Convenience constructors for common test scenarios.


def timeout_provider() -> FakeAIProvider:
    return FakeAIProvider(raise_error=AIProviderTimeoutError("simulated timeout"))


def auth_error_provider() -> FakeAIProvider:
    return FakeAIProvider(raise_error=AIProviderAuthError("simulated auth failure"))


def rate_limit_provider() -> FakeAIProvider:
    return FakeAIProvider(raise_error=AIProviderRateLimitError("simulated rate limit"))


def unavailable_provider() -> FakeAIProvider:
    return FakeAIProvider(raise_error=AIProviderUnavailableError("simulated provider outage"))


def malformed_provider() -> FakeAIProvider:
    return FakeAIProvider(malformed=True)
