"""GET /api/v1/system/info — how this deployment is wired.

Read-only. Lets the dashboard label itself honestly (DEMO MODE when
payment execution is simulated, a real gateway otherwise) and render the
AI -> policy comparison against the same thresholds the backend enforces,
instead of hard-coding them in the frontend where they could drift.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.system import PolicyLimits, SystemInfoResponse

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/info", response_model=SystemInfoResponse)
async def get_system_info() -> SystemInfoResponse:
    s = get_settings()

    has_razorpay = bool(s.razorpay_key_id and s.razorpay_key_secret)
    payment_provider = "razorpay" if has_razorpay else "fake"
    payment_provider_mode = s.razorpay_mode if has_razorpay else "simulated"
    ai_provider = "groq" if s.groq_api_key else "unconfigured"

    return SystemInfoResponse(
        app_env=s.app_env,
        demo_mode=not has_razorpay,
        payment_provider=payment_provider,
        payment_provider_mode=payment_provider_mode,
        ai_provider=ai_provider,
        ai_model=s.groq_model,
        orchestrator_enabled=s.orchestrator_enabled,
        orchestrator_auto_execute=s.orchestrator_auto_execute,
        auth_enforced=bool(s.api_key),
        policy=PolicyLimits(
            max_retry_count=s.policy_max_retry_count,
            max_recovery_window_days=s.policy_max_recovery_window_days,
            max_customer_contacts=s.policy_max_customer_contacts,
            min_confidence_threshold=s.policy_min_confidence_threshold,
            high_value_amount_threshold=s.policy_high_value_amount_threshold,
            high_value_min_confidence_threshold=s.policy_high_value_min_confidence_threshold,
            max_recovery_amount=s.policy_max_recovery_amount,
        ),
    )
