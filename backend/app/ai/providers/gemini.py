"""Google Gemini provider, via the `google-genai` SDK's Interactions API.

Verified against the currently published Gemini API/SDK docs during Phase 2
development (see docs/ai-architecture.md for exactly what was checked),
plus a live request against the real API with an intentionally invalid key
to confirm the request shape is accepted end to end (got back a genuine
401-style API error, not a client-side validation error).

Exception classification note: `client.aio.interactions.create` raises
exception classes from a private module
(`google.genai._gaos.lib.compat_errors`), not the public
`google.genai.errors.APIError` hierarchy used by the older
`client.models.generate_content` API. Importing a private module isn't
safe against SDK internals changing, so this file classifies failures by
exception *class name* instead of `isinstance` — informational only, and
deliberately fails closed: anything unrecognized is treated as
"unavailable" so the caller falls back rather than assuming success.
"""
from __future__ import annotations

import json
import logging
import time

from google import genai
from google.genai import interactions as genai_interactions
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

_TIMEOUT_EXCEPTION_NAMES = {"APITimeoutError", "TimeoutError"}
_AUTH_EXCEPTION_NAMES = {"AuthenticationError", "PermissionDeniedError"}
_RATE_LIMIT_EXCEPTION_NAMES = {"RateLimitError"}
_TRANSIENT_EXCEPTION_NAMES = {"APIConnectionError", "InternalServerError", "NoResponseError"}


class GeminiProvider(AIProvider):
    def __init__(self, *, api_key: str, model: str, timeout_seconds: float) -> None:
        if not api_key:
            raise ValueError("GeminiProvider requires a non-empty API key.")
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def diagnose_payment(self, context: PaymentRecoveryContext) -> RecoveryRecommendation:
        started = time.perf_counter()
        try:
            interaction = await self._client.aio.interactions.create(
                model=self._model,
                system_instruction=SYSTEM_INSTRUCTION,
                input=build_user_input(context),
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": RecoveryRecommendation.model_json_schema(),
                },
                timeout=self._timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - classified below, never re-raised raw
            raise self._classify(exc) from exc

        # mypy sees `create()`'s return type as a TypeAlias union with the
        # streaming response type (because streaming is possible via
        # stream=True, which this provider never passes); at runtime
        # `Interaction` is a normal pydantic model class and isinstance()
        # works correctly here (verified directly against the installed
        # SDK) — narrowing for mypy's benefit, not a real ambiguity.
        if not isinstance(interaction, genai_interactions.Interaction):  # type: ignore[arg-type]
            raise AIProviderInvalidResponseError(
                f"Expected a non-streaming Interaction, got {type(interaction).__name__}."
            )

        latency_ms = (time.perf_counter() - started) * 1000
        self._log_success(interaction, latency_ms)

        # getattr, not attribute access: mypy resolves create()'s return
        # type as a union with the streaming-response type (see comment
        # above), so it can't statically see these attributes even after
        # the isinstance check above narrows them at runtime.
        status = getattr(interaction, "status", None)
        output_text = getattr(interaction, "output_text", None)
        if status != "completed" or not output_text:
            raise AIProviderInvalidResponseError(
                f"Gemini interaction did not complete successfully (status={status!r})."
            )

        try:
            return RecoveryRecommendation.model_validate_json(output_text)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise AIProviderInvalidResponseError(f"Gemini output failed schema validation: {exc}") from exc

    def _log_success(self, interaction: object, latency_ms: float) -> None:
        usage = getattr(interaction, "usage", None)
        log_event(
            logger,
            logging.INFO,
            "gemini_interaction_completed",
            model=self._model,
            prompt_version=PROMPT_VERSION,
            latency_ms=round(latency_ms, 1),
            status=getattr(interaction, "status", None),
            input_tokens=getattr(usage, "total_input_tokens", None),
            output_tokens=getattr(usage, "total_output_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
        )

    @staticmethod
    def _classify(exc: Exception) -> AIProviderError:
        name = type(exc).__name__
        message = str(exc)

        if isinstance(exc, TimeoutError) or name in _TIMEOUT_EXCEPTION_NAMES:
            return AIProviderTimeoutError(f"Gemini request timed out: {message}")
        if name in _AUTH_EXCEPTION_NAMES:
            return AIProviderAuthError(f"Gemini authentication failed: {message}")
        if name in _RATE_LIMIT_EXCEPTION_NAMES:
            return AIProviderRateLimitError(f"Gemini rate limit exceeded: {message}")
        if name in _TRANSIENT_EXCEPTION_NAMES:
            return AIProviderUnavailableError(f"Gemini provider unavailable: {message}")

        # Includes BadRequestError, UnprocessableEntityError, and anything
        # unrecognized. A malformed request is a bug on our side, but the
        # safe behavior is still "don't automate" — never assume success.
        return AIProviderUnavailableError(f"Gemini request failed ({name}): {message}")
