"""POST /api/v1/demo/seed-batch — run the measured recovery batch from the UI.

The batch itself is `scripts.seed_demo_batch`; this exposes it so the
Track 03 bar ("show measured money recovered across a batch") is
demonstrable by anyone who can open the dashboard, not only by someone
with a terminal in the right directory.

What runs here:

- **AI diagnosis is live.** The endpoint injects the same `get_ai_service`
  the real diagnose endpoint uses — with `GROQ_API_KEY` set, every case
  gets a real `openai/gpt-oss-120b` diagnosis; with no key, the safe
  ESCALATE fallback, exactly as in production.
- **Execution + webhook are simulated** — `FakePaymentProvider` stands in
  for Razorpay (not configured in this environment) so a case can reach
  `RECOVERED` without a live gateway. Everything between (policy, state
  machine, aggregation, audit trail) is the real code path.

Two guards, because this writes real rows:

- **Refused in production.** `APP_ENV=production` returns 403 — seeding
  fabricated cases into a production database would corrupt the very
  metric the endpoint exists to demonstrate.
- **Provenance is in the response,** not left to the caller: every
  response states which parts were live and which were simulated.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.dependencies import get_ai_service
from app.ai.service import AIRecommendationService
from app.core.config import get_settings
from app.core.logging import get_correlation_id
from app.db.session import get_db
from app.schemas.demo import DemoBatchResponse
from app.services import recovery_summary_service
from scripts.seed_demo_batch import run_demo_batch

router = APIRouter(prefix="/demo", tags=["demo"])

_PROVENANCE = (
    "AI diagnosis: LIVE — real provider calls (or the safe ESCALATE fallback when no "
    "key is configured). Execution + webhook confirmation: SIMULATED via "
    "FakePaymentProvider, because Razorpay Test Mode is not configured here. The case "
    "inputs, policy decisions, state transitions, audit trail, and the recovery-rate "
    "arithmetic are all real; the payment that confirms a recovery is not. Never quote "
    "the recovered figure as a Razorpay Test Mode result."
)


@router.post("/seed-batch", response_model=DemoBatchResponse)
async def seed_demo_batch(
    session: AsyncSession = Depends(get_db),
    ai_service: AIRecommendationService = Depends(get_ai_service),
) -> DemoBatchResponse:
    """Seed and process a mixed batch of recovery cases through the real
    pipeline, then return the measured recovery summary computed from the
    resulting rows."""
    settings = get_settings()
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo seeding is disabled in production — it writes fabricated recovery cases.",
        )

    correlation_id = get_correlation_id() or str(uuid.uuid4())
    outcome = await run_demo_batch(session, ai_service=ai_service)

    by_status: dict[str, int] = {}
    for case_status in outcome.final_status_by_case.values():
        by_status[case_status.value] = by_status.get(case_status.value, 0) + 1

    summary = await recovery_summary_service.compute_recovery_summary(session)

    return DemoBatchResponse(
        correlation_id=correlation_id,
        cases_processed=len(outcome.case_ids),
        final_status_counts=by_status,
        ai_model=outcome.ai_model,
        summary=summary,
        provenance=_PROVENANCE,
    )
