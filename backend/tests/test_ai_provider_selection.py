"""`get_ai_provider` builds the real provider when configured, and degrades
to a safe stand-in when it isn't — never crashing the app on a missing key.
"""
from __future__ import annotations

import pytest

from app.ai.dependencies import _UnconfiguredProvider, get_ai_provider, get_ai_service
from app.ai.providers.base import AIProviderAuthError
from app.ai.providers.groq import GroqProvider
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _clear_cache():
    get_ai_provider.cache_clear()
    yield
    get_ai_provider.cache_clear()


def _set(monkeypatch, **overrides):
    s = get_settings()
    for k, v in overrides.items():
        monkeypatch.setattr(s, k, v)


def test_configured_key_builds_the_real_provider(monkeypatch):
    _set(monkeypatch, groq_api_key="gsk_test", groq_model="openai/gpt-oss-120b")
    assert isinstance(get_ai_provider(), GroqProvider)


def test_missing_key_degrades_safely_instead_of_crashing(monkeypatch):
    _set(monkeypatch, groq_api_key="")
    assert isinstance(get_ai_provider(), _UnconfiguredProvider)


@pytest.mark.asyncio
async def test_the_stand_in_raises_an_auth_error_the_service_can_absorb(monkeypatch):
    """AIRecommendationService turns this into a safe ESCALATE fallback —
    so a deployment with no key still works, it just never automates."""
    _set(monkeypatch, groq_api_key="")
    provider = get_ai_provider()
    with pytest.raises(AIProviderAuthError, match="GROQ_API_KEY"):
        await provider.diagnose_payment(None)  # type: ignore[arg-type]


def test_service_records_the_configured_model_name(monkeypatch):
    """The model name is written into every AI audit event, so it must be
    the one actually in use."""
    _set(monkeypatch, groq_api_key="gsk_test", groq_model="openai/gpt-oss-120b")
    service = get_ai_service()
    assert service._model_name == "openai/gpt-oss-120b"


def test_provider_is_cached_so_one_http_client_is_reused(monkeypatch):
    _set(monkeypatch, groq_api_key="gsk_test")
    assert get_ai_provider() is get_ai_provider()
