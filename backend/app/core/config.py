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

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    ai_request_timeout_seconds: float = Field(default=20.0, gt=0)
    ai_max_retries: int = Field(default=1, ge=0)

    policy_max_retry_count: int = Field(default=3, ge=0)
    policy_max_recovery_window_days: int = Field(default=14, ge=1)
    policy_max_customer_contacts: int = Field(default=2, ge=0)
    policy_min_confidence_threshold: float = Field(default=0.55, ge=0.0, le=1.0)

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
