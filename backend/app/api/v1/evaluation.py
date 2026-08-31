from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.evaluation import EvaluationSummaryRead, RecoverySummaryRead
from app.services import evaluation_service, recovery_summary_service

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


@router.get("/summary", response_model=EvaluationSummaryRead)
async def get_evaluation_summary() -> EvaluationSummaryRead:
    """The synthetic-dataset evaluation report (evaluation/run_evaluation.py
    / run_ai_evaluation.py) — hundreds of simulated cases, not live data."""
    return evaluation_service.get_latest_summary()


@router.get("/recovery-summary", response_model=RecoverySummaryRead)
async def get_recovery_summary(session: AsyncSession = Depends(get_db)) -> RecoverySummaryRead:
    """Real metrics computed live from this database's `recovery_cases` —
    however many actually exist here, never mixed with the simulated
    numbers above. See docs/razorpay-integration.md."""
    return await recovery_summary_service.compute_recovery_summary(session)
