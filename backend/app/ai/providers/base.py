"""AI provider interface.

Any provider (Gemini today, others later) must implement `diagnose_payment`
and raise only `AIProviderError` subclasses on failure — callers
(`app.ai.service.AIRecommendationService`) never need to know which
concrete provider or SDK is behind this interface, and never see a raw
provider exception. A provider either returns a validated
`RecoveryRecommendation` or raises; it never returns `None` or a partial
result.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.ai.context import PaymentRecoveryContext
from app.ai.schemas import RecoveryRecommendation


class AIProviderError(Exception):
    """Base class for all AI provider failures. Always caught at the
    `AIRecommendationService` boundary — never allowed to propagate into
    the API layer or crash a request."""


class AIProviderTimeoutError(AIProviderError):
    pass


class AIProviderAuthError(AIProviderError):
    pass


class AIProviderRateLimitError(AIProviderError):
    pass


class AIProviderUnavailableError(AIProviderError):
    """Network failure, 5xx, or any other transient provider-side issue."""


class AIProviderInvalidResponseError(AIProviderError):
    """The provider responded, but its output didn't parse or didn't pass
    `RecoveryRecommendation` validation (bad enum value, out-of-range
    confidence, non-JSON body, incomplete interaction, ...)."""


class AIProvider(ABC):
    @abstractmethod
    async def diagnose_payment(self, context: PaymentRecoveryContext) -> RecoveryRecommendation:
        """Returns a validated recommendation or raises an AIProviderError subclass."""
        raise NotImplementedError
