"""Guards the Settings -> PolicyConfig mapping.

A `PolicyConfig` field that `get_policy_config` forgets to pass is
silently pinned to its dataclass default: the environment variable exists,
the operator sets it, and nothing happens. That is exactly the bug this
file exists to make impossible — `test_every_policy_config_field_is_wired`
fails the moment a new field is added without wiring it.
"""
from __future__ import annotations

import dataclasses

from app.core.config import Settings
from app.domain.policy import PolicyConfig
from app.services.policy_service import get_policy_config

# `policy_version` is a constant stamped onto every decision for audit
# purposes, not an operator-tunable threshold, so it is deliberately not
# driven by a setting.
_NOT_OPERATOR_CONFIGURABLE = {"policy_version"}


def _settings_with_distinct_policy_values() -> Settings:
    """Settings whose every policy_* value differs from the PolicyConfig
    dataclass default, so a missed wiring shows up as "still the default"."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        policy_max_retry_count=7,
        policy_max_recovery_window_days=21,
        policy_max_customer_contacts=5,
        policy_min_confidence_threshold=0.42,
        policy_high_value_amount_threshold=123_456,
        policy_high_value_min_confidence_threshold=0.91,
        policy_max_recovery_amount=777_777,
    )


def test_every_policy_config_field_is_wired():
    defaults = PolicyConfig()
    config = get_policy_config(_settings_with_distinct_policy_values())

    unwired = [
        field.name
        for field in dataclasses.fields(PolicyConfig)
        if field.name not in _NOT_OPERATOR_CONFIGURABLE
        and getattr(config, field.name) == getattr(defaults, field.name)
    ]

    assert not unwired, (
        f"PolicyConfig field(s) {unwired} are not wired through "
        "policy_service.get_policy_config — the matching environment "
        "variable would silently do nothing. Add them there (and to "
        "Settings) or list them in _NOT_OPERATOR_CONFIGURABLE."
    )


def test_values_are_carried_through_unchanged():
    settings = _settings_with_distinct_policy_values()
    config = get_policy_config(settings)

    assert config.max_retry_count == settings.policy_max_retry_count
    assert config.max_recovery_window_days == settings.policy_max_recovery_window_days
    assert config.max_customer_contacts == settings.policy_max_customer_contacts
    assert config.min_confidence_threshold == settings.policy_min_confidence_threshold
    assert config.high_value_amount_threshold == settings.policy_high_value_amount_threshold
    assert (
        config.high_value_min_confidence_threshold
        == settings.policy_high_value_min_confidence_threshold
    )
    assert config.max_recovery_amount == settings.policy_max_recovery_amount


def test_policy_version_is_always_stamped():
    config = get_policy_config(_settings_with_distinct_policy_values())
    assert config.policy_version == PolicyConfig().policy_version
