"""GroqProvider: request shape, output validation, and exception mapping.

No network — a stub AsyncGroq stands in. What matters is that every groq
SDK failure class maps to the right `AIProviderError` subtype, and that
malformed model output is rejected here rather than propagated.
"""
from __future__ import annotations

import json

import pytest
from groq import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from app.ai.context import CustomerPaymentHistory, PaymentRecoveryContext
from app.ai.providers.base import (
    AIProviderAuthError,
    AIProviderInvalidResponseError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
)
from app.ai.providers.groq import GroqProvider

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

_VALID_JSON = json.dumps(
    {
        "diagnosis_category": "temporary_failure",
        "recovery_confidence": 0.82,
        "recommended_action": "retry_payment",
        "decision_explanation": "Transient network error, first attempt, healthy history.",
    }
)


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _Msg(content)
        self.finish_reason = finish_reason


class _Completion:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = None


class _StubCompletions:
    def __init__(self, *, content=None, raises=None):
        self._content = content
        self._raises = raises
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return _Completion(self._content)


class _StubChat:
    def __init__(self, completions):
        self.completions = completions


def _provider_with(stub_completions) -> GroqProvider:
    p = GroqProvider(api_key="test-key", model="openai/gpt-oss-120b", timeout_seconds=30)
    p._client.chat = _StubChat(stub_completions)  # type: ignore[attr-defined]
    return p


# --- happy path ---


@pytest.mark.asyncio
async def test_valid_json_output_parses_to_a_recommendation():
    stub = _StubCompletions(content=_VALID_JSON)
    rec = await _provider_with(stub).diagnose_payment(CONTEXT)
    assert rec.recommended_action.value == "retry_payment"
    assert rec.recovery_confidence == 0.82


@pytest.mark.asyncio
async def test_request_uses_json_object_response_format_and_the_versioned_prompt():
    stub = _StubCompletions(content=_VALID_JSON)
    await _provider_with(stub).diagnose_payment(CONTEXT)
    (call,) = stub.calls
    assert call["response_format"] == {"type": "json_object"}
    assert call["model"] == "openai/gpt-oss-120b"
    roles = [m["role"] for m in call["messages"]]
    assert roles == ["system", "user"]
    # the case data must be in the user turn, not the system instruction
    assert "PAYMENT RECOVERY CONTEXT" in call["messages"][1]["content"]
    assert "RecoverAI's payment-recovery diagnosis component" in call["messages"][0]["content"]
    # Groq's json_object mode requires the word "json" in the messages.
    assert "json" in call["messages"][0]["content"].lower()


# --- output validation ---


@pytest.mark.asyncio
async def test_non_json_output_is_rejected():
    stub = _StubCompletions(content="here is my answer: retry the payment")
    with pytest.raises(AIProviderInvalidResponseError):
        await _provider_with(stub).diagnose_payment(CONTEXT)


@pytest.mark.asyncio
async def test_out_of_range_confidence_is_rejected():
    bad = json.dumps(
        {
            "diagnosis_category": "temporary_failure",
            "recovery_confidence": 1.7,
            "recommended_action": "retry_payment",
            "decision_explanation": "x",
        }
    )
    with pytest.raises(AIProviderInvalidResponseError):
        await _provider_with(_StubCompletions(content=bad)).diagnose_payment(CONTEXT)


@pytest.mark.asyncio
async def test_unknown_action_enum_is_rejected():
    bad = json.dumps(
        {
            "diagnosis_category": "temporary_failure",
            "recovery_confidence": 0.5,
            "recommended_action": "call_the_customer_personally",
            "decision_explanation": "x",
        }
    )
    with pytest.raises(AIProviderInvalidResponseError):
        await _provider_with(_StubCompletions(content=bad)).diagnose_payment(CONTEXT)


@pytest.mark.asyncio
async def test_empty_completion_is_rejected():
    with pytest.raises(AIProviderInvalidResponseError):
        await _provider_with(_StubCompletions(content="")).diagnose_payment(CONTEXT)


# --- exception classification ---


def _groq_exc(cls):
    """Build a groq SDK exception without a real HTTP round-trip."""
    import httpx

    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    if cls in (APITimeoutError, APIConnectionError):
        return cls(request=req)
    resp = httpx.Response(status_code=500, request=req)
    return cls("boom", response=resp, body=None)


@pytest.mark.parametrize(
    ("groq_cls", "expected"),
    [
        (APITimeoutError, AIProviderTimeoutError),
        (AuthenticationError, AIProviderAuthError),
        (PermissionDeniedError, AIProviderAuthError),
        (RateLimitError, AIProviderRateLimitError),
        (APIConnectionError, AIProviderUnavailableError),
        (InternalServerError, AIProviderUnavailableError),
    ],
)
@pytest.mark.asyncio
async def test_sdk_exceptions_map_to_the_right_provider_error(groq_cls, expected):
    stub = _StubCompletions(raises=_groq_exc(groq_cls))
    with pytest.raises(expected):
        await _provider_with(stub).diagnose_payment(CONTEXT)


@pytest.mark.asyncio
async def test_an_unrecognized_error_fails_closed_as_unavailable():
    stub = _StubCompletions(raises=RuntimeError("something the SDK never documented"))
    with pytest.raises(AIProviderUnavailableError):
        await _provider_with(stub).diagnose_payment(CONTEXT)


@pytest.mark.asyncio
async def test_provider_never_leaks_a_raw_sdk_exception():
    """Contract from AIProvider: only AIProviderError subclasses escape."""
    from app.ai.providers.base import AIProviderError

    stub = _StubCompletions(raises=_groq_exc(RateLimitError))
    with pytest.raises(AIProviderError):
        await _provider_with(stub).diagnose_payment(CONTEXT)


def test_construction_requires_a_key():
    with pytest.raises(ValueError):
        GroqProvider(api_key="", model="openai/gpt-oss-120b", timeout_seconds=30)
