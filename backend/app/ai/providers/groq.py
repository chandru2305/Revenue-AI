"""Groq provider, via the `groq` SDK's OpenAI-compatible chat-completions API.

The real implementation of `AIProvider`; `FakeAIProvider` is the other.
The caller (`AIRecommendationService`) never learns which SDK is behind
the interface, so swapping vendors means adding one file here and
changing `app.ai.dependencies` — nothing in the services or API layer
moves.

Verified live on 1 Sep 2026 against `openai/gpt-oss-120b`: valid, varied,
case-appropriate recommendations at ~1.3s mean end-to-end. Groq's
`response_format={"type": "json_object"}` guarantees valid JSON but does
NOT enforce a schema, so the parsed object is validated against
`RecoveryRecommendation` here rather than trusted.

Exception classification uses `isinstance` against the SDK's public
exception classes. Anything unrecognized is treated as "unavailable" so
the caller falls back rather than assuming success.
"""
from __future__ import annotations

import json
import logging
import time

from groq import (
    APIConnectionError,
    APITimeoutError,
    AsyncGroq,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from groq import (
    BadRequestError as GroqBadRequestError,
)
from pydantic import ValidationError

from app.ai.context import PaymentRecoveryContext
from app.ai.prompts.diagnosis_v1 import PROMPT_VERSION, SYSTEM_INSTRUCTION, build_user_input
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
from app.core.logging import get_logger, log_event

logger = get_logger(__name__)

# Groq rejects `response_format={"type": "json_object"}` unless the word
# "json" appears somewhere in the messages (a documented API constraint).
# The versioned prompt (`diagnosis_v1`) predates any Groq support and must
# not be edited in place — its wording is pinned to the audit trail — so
# this provider appends the requirement itself. It changes nothing about
# what is asked, only how it is phrased for one API.
_GROQ_JSON_DIRECTIVE = (
    "\n\nOutput format: respond with a single JSON object matching the "
    "required schema, and nothing else."
)


class GroqProvider(AIProvider):
    def __init__(self, *, api_key: str, model: str, timeout_seconds: float) -> None:
        if not api_key:
            raise ValueError("GroqProvider requires a non-empty API key.")
        self._client = AsyncGroq(api_key=api_key)
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def diagnose_payment(self, context: PaymentRecoveryContext) -> RecoveryRecommendation:
        started = time.perf_counter()
        try:
            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_INSTRUCTION + _GROQ_JSON_DIRECTIVE},
                    {"role": "user", "content": build_user_input(context)},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                timeout=self._timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - classified below, never re-raised raw
            raise self._classify(exc) from exc

        latency_ms = (time.perf_counter() - started) * 1000
        content = completion.choices[0].message.content if completion.choices else None

        usage = getattr(completion, "usage", None)
        log_event(
            logger,
            logging.INFO,
            "groq_completion",
            model=self._model,
            prompt_version=PROMPT_VERSION,
            latency_ms=round(latency_ms, 1),
            finish_reason=(completion.choices[0].finish_reason if completion.choices else None),
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )

        if not content:
            raise AIProviderInvalidResponseError("Groq returned an empty completion.")

        try:
            return RecoveryRecommendation.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise AIProviderInvalidResponseError(
                f"Groq output failed schema validation: {exc}"
            ) from exc

    @staticmethod
    def _classify(exc: Exception) -> AIProviderError:
        message = str(exc)
        if isinstance(exc, APITimeoutError | TimeoutError):
            return AIProviderTimeoutError(f"Groq request timed out: {message}")
        if isinstance(exc, AuthenticationError | PermissionDeniedError):
            return AIProviderAuthError(f"Groq authentication failed: {message}")
        if isinstance(exc, RateLimitError):
            return AIProviderRateLimitError(f"Groq rate limit exceeded: {message}")
        if isinstance(exc, APIConnectionError | InternalServerError):
            return AIProviderUnavailableError(f"Groq provider unavailable: {message}")
        # BadRequestError and anything unrecognized: a malformed request is
        # a bug on our side, but the safe behavior is still "don't
        # automate" — never assume success.
        name = type(exc).__name__
        if isinstance(exc, GroqBadRequestError):
            name = "BadRequestError"
        return AIProviderUnavailableError(f"Groq request failed ({name}): {message}")
