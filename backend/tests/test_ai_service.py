import pytest

from app.ai.context import CustomerPaymentHistory, PaymentRecoveryContext
from app.ai.providers.base import AIProviderAuthError, AIProviderInvalidResponseError, AIProviderTimeoutError
from app.ai.providers.fake import FakeAIProvider
from app.ai.schemas import RecoveryRecommendation
from app.ai.service import AIRecommendationService
from app.domain.enums import DecisionSource, DiagnosisCategory, RecoveryAction

CONTEXT = PaymentRecoveryContext(
    payment_id="pay-1",
    amount=10_000,
    currency="INR",
    failure_reason="network_error",
    payment_method="card",
    attempt_number=1,
    customer_payment_history=CustomerPaymentHistory(successful_payments=3, failed_payments=0),
    previous_recovery_actions=[],
    time_since_failure_minutes=10,
)

VALID_RECOMMENDATION = RecoveryRecommendation(
    diagnosis_category=DiagnosisCategory.TEMPORARY_FAILURE,
    recovery_confidence=0.87,
    recommended_action=RecoveryAction.RETRY_PAYMENT,
    decision_explanation="Temporary failure, first attempt, positive history.",
)


@pytest.mark.asyncio
async def test_successful_call_returns_ai_sourced_outcome():
    provider = FakeAIProvider(recommendation=VALID_RECOMMENDATION)
    service = AIRecommendationService(provider, model_name="openai/gpt-oss-120b", max_retries=1)

    outcome = await service.get_recommendation(CONTEXT)

    assert outcome.decision_source == DecisionSource.AI
    assert outcome.recommendation == VALID_RECOMMENDATION
    assert outcome.model == "openai/gpt-oss-120b"
    assert outcome.failure_code is None
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_timeout_falls_back_to_escalate_after_exhausting_retries():
    provider = FakeAIProvider(raise_error=AIProviderTimeoutError("simulated timeout"))
    service = AIRecommendationService(provider, model_name="openai/gpt-oss-120b", max_retries=2)

    outcome = await service.get_recommendation(CONTEXT)

    assert outcome.decision_source == DecisionSource.FALLBACK
    assert outcome.recommendation.recommended_action == RecoveryAction.ESCALATE
    assert outcome.recommendation.recovery_confidence == 0.0
    assert outcome.failure_code == "AIProviderTimeoutError"
    # 1 initial attempt + 2 retries = 3 calls.
    assert provider.call_count == 3


@pytest.mark.asyncio
async def test_auth_error_does_not_retry():
    provider = FakeAIProvider(raise_error=AIProviderAuthError("bad key"))
    service = AIRecommendationService(provider, model_name="openai/gpt-oss-120b", max_retries=2)

    outcome = await service.get_recommendation(CONTEXT)

    assert outcome.decision_source == DecisionSource.FALLBACK
    assert outcome.failure_code == "AIProviderAuthError"
    # Auth failures are not transient — must not retry.
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_malformed_response_falls_back_without_retry():
    provider = FakeAIProvider(malformed=True)
    service = AIRecommendationService(provider, model_name="openai/gpt-oss-120b", max_retries=2)

    outcome = await service.get_recommendation(CONTEXT)

    assert outcome.decision_source == DecisionSource.FALLBACK
    assert outcome.failure_code == "AIProviderInvalidResponseError"
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_zero_max_retries_still_falls_back_safely():
    provider = FakeAIProvider(raise_error=AIProviderTimeoutError("simulated timeout"))
    service = AIRecommendationService(provider, model_name="openai/gpt-oss-120b", max_retries=0)

    outcome = await service.get_recommendation(CONTEXT)

    assert outcome.decision_source == DecisionSource.FALLBACK
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_fallback_never_raises_even_on_unexpected_error_type():
    provider = FakeAIProvider(raise_error=AIProviderInvalidResponseError("weird shape"))
    service = AIRecommendationService(provider, model_name="openai/gpt-oss-120b", max_retries=1)

    # Must not raise — the whole point of this service is that it never
    # propagates a provider failure to the caller.
    outcome = await service.get_recommendation(CONTEXT)
    assert outcome.decision_source == DecisionSource.FALLBACK
