"""Typed application configuration loaded from environment variables / .env.

All configuration must flow through this module. Never read os.environ
directly elsewhere in the application.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. Instantiate once via get_settings()."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "RecoverAI"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_v1_prefix: str = "/api/v1"

    cors_allow_origins: str = "http://localhost:5173"

    # Shared API key for the versioned API, sent as `X-API-Key`. Empty
    # means auth is not enforced (development convenience) — except under
    # APP_ENV=production, where an empty key refuses to start. See
    # app/core/auth.py and docs/security.md.
    api_key: str = ""

    database_url: str = "postgresql+asyncpg://recoverai:recoverai@localhost:5432/recoverai"

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    razorpay_base_url: str = "https://api.razorpay.com/v1"
    # Hard safety guard (see app/payments/providers/razorpay.py): the
    # provider refuses to run unless this is exactly "test". Never flip to
    # "live" without a deliberate, reviewed change.
    razorpay_mode: Literal["test", "live"] = "test"
    razorpay_request_timeout_seconds: float = Field(default=15.0, gt=0)
    razorpay_max_retries: int = Field(default=1, ge=0)
    # How long a created Payment Link stays valid before we consider the
    # recovery attempt expired. Deliberately much shorter than Razorpay's
    # own 6-month default and than the policy engine's 14-day recovery
    # window, since a stale, forgotten link is worse than re-diagnosing.
    recovery_payment_link_expiry_hours: int = Field(default=72, ge=1)

    # Groq backs AI diagnosis. Empty key -> the diagnose endpoint degrades
    # to the safe ESCALATE fallback rather than crashing.
    groq_api_key: str = ""
    # Pinned, not a floating alias: the model name is written into every
    # AI-sourced audit event, so a moving target would make a recorded
    # decision unreproducible. Verified live on 1 Sep 2026 via the
    # chat-completions + json_object call path — varied, case-appropriate
    # output at ~0.5s server time.
    groq_model: str = "openai/gpt-oss-120b"
    # Generous relative to Groq's observed ~1.3s mean, because a timeout
    # that trips on ordinary latency doesn't protect anything — it
    # discards a good recommendation and escalates to a human instead.
    # Diagnosis is not latency-critical (an operator action or a
    # background sweep), so headroom is cheap.
    ai_request_timeout_seconds: float = Field(default=45.0, gt=0)
    ai_max_retries: int = Field(default=1, ge=0)

    # --- Autonomous recovery loop (app/services/orchestrator_service.py) ---
    # Run cycles automatically in the background. Off by default so a dev
    # server, a test run, and `make up` never start making provider calls
    # on their own; `POST /api/v1/orchestrator/cycle` always works
    # regardless, for an operator-triggered pass.
    orchestrator_enabled: bool = False
    orchestrator_interval_seconds: int = Field(default=300, ge=10)
    # SAFETY: executing moves money. Diagnosis is reasoning plus a policy
    # decision and is safe to automate; execution is deliberately opt-in
    # and defaults OFF, leaving approved cases for a human. Turning this on
    # does NOT bypass any policy rule — execution still re-checks policy
    # with fresh data before calling the provider.
    orchestrator_auto_execute: bool = False
    # Per-cycle budgets, so a large backlog drains gradually rather than
    # firing hundreds of provider calls in one pass.
    orchestrator_max_discover: int = Field(default=100, ge=1)
    orchestrator_max_diagnose: int = Field(default=25, ge=1)
    orchestrator_max_execute: int = Field(default=10, ge=0)

    # Every one of these maps to a field on app.domain.policy.PolicyConfig
    # via app.services.policy_service.get_policy_config. Adding a field
    # there without adding it here (and wiring it) makes it silently
    # un-configurable — see test_policy_service.py.
    policy_max_retry_count: int = Field(default=3, ge=0)
    policy_max_recovery_window_days: int = Field(default=14, ge=1)
    policy_max_customer_contacts: int = Field(default=2, ge=0)
    policy_min_confidence_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    policy_high_value_amount_threshold: int = Field(default=500_000, ge=1)
    policy_high_value_min_confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    policy_max_recovery_amount: int = Field(default=10_000_000, ge=1)

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value:
            raise ValueError("DATABASE_URL must not be empty")
        return value

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
