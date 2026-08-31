"""Builds the active PolicyConfig from application settings."""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.domain.policy import PolicyConfig


def get_policy_config(settings: Settings | None = None) -> PolicyConfig:
    settings = settings or get_settings()
    return PolicyConfig(
        max_retry_count=settings.policy_max_retry_count,
        max_recovery_window_days=settings.policy_max_recovery_window_days,
        max_customer_contacts=settings.policy_max_customer_contacts,
        min_confidence_threshold=settings.policy_min_confidence_threshold,
    )
