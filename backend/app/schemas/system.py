"""Response schema for GET /api/v1/system/info.

A small, read-only description of how this deployment is wired — which
provider modes are live, whether the autonomous loop is on, and the
deterministic policy limits. The dashboard uses it to label itself
honestly (DEMO MODE vs. a real gateway) and to render the AI -> policy
comparison against the same thresholds the backend enforces.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PolicyLimits(BaseModel):
    """The deterministic thresholds `app.domain.policy.evaluate_policy`
    enforces, surfaced so the UI can show each check with the real number
    rather than a hard-coded guess."""

    max_retry_count: int
    max_recovery_window_days: int
    max_customer_contacts: int
    min_confidence_threshold: float
    high_value_amount_threshold: int
    high_value_min_confidence_threshold: float
    max_recovery_amount: int


class SystemInfoResponse(BaseModel):
    app_env: str

    demo_mode: bool = Field(
        description="True when payment execution is simulated (no Razorpay Test Mode key). "
        "The recovered-revenue figure is a real measurement over real rows whose payment "
        "confirmation was simulated — never a Razorpay result."
    )
    payment_provider: str = Field(description='"razorpay" or "fake".')
    payment_provider_mode: str = Field(description='"test", "live", or "simulated".')

    ai_provider: str = Field(description='"groq" or "unconfigured".')
    ai_model: str

    orchestrator_enabled: bool
    orchestrator_auto_execute: bool
    auth_enforced: bool = Field(description="Whether an API key is required on /api/v1.")

    policy: PolicyLimits
