"""Computes real recovery metrics from the actual `recovery_cases` table.

Deliberately separate from `evaluation_service` (which only ever surfaces
a JSON report from the synthetic-dataset harness): this module queries
the live database, so its numbers reflect whatever cases genuinely exist
in this deployment — a small, real set, not 500+ simulated ones. Never
combine the two without labeling which is which — see
docs/razorpay-integration.md "Simulated vs. real evaluation."

`recovery_rate` is confirmed-recovered / eligible — a created Payment Link
is never counted as recovered revenue.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import RecoveryAttemptStatus, RecoveryCaseStatus
from app.models.recovery_attempt import RecoveryAttempt
from app.models.recovery_case import RecoveryCase
from app.schemas.evaluation import RecoverySummaryRead


async def compute_recovery_summary(session: AsyncSession) -> RecoverySummaryRead:
    cases_total = (await session.execute(select(func.count()).select_from(RecoveryCase))).scalar_one()

    status_rows = (
        await session.execute(select(RecoveryCase.status, func.count()).group_by(RecoveryCase.status))
    ).all()
    cases_by_status: dict[str, int] = {str(status): count for status, count in status_rows}

    cases_eligible = (
        await session.execute(
            select(func.count()).select_from(RecoveryCase).where(RecoveryCase.eligible.is_(True))
        )
    ).scalar_one()

    total_revenue_at_risk = (
        await session.execute(select(func.coalesce(func.sum(RecoveryCase.revenue_at_risk), 0)))
    ).scalar_one()

    eligible_revenue = (
        await session.execute(
            select(func.coalesce(func.sum(RecoveryCase.revenue_at_risk), 0)).where(
                RecoveryCase.eligible.is_(True)
            )
        )
    ).scalar_one()

    confirmed_recovered_revenue = (
        await session.execute(select(func.coalesce(func.sum(RecoveryCase.recovered_amount), 0)))
    ).scalar_one()

    recovered_case_count = (
        await session.execute(
            select(func.count()).select_from(RecoveryCase).where(RecoveryCase.recovered_amount > 0)
        )
    ).scalar_one()

    recovery_attempts = (
        await session.execute(select(func.count()).select_from(RecoveryAttempt))
    ).scalar_one()

    successful_links = (
        await session.execute(
            select(func.count())
            .select_from(RecoveryAttempt)
            .where(RecoveryAttempt.status == RecoveryAttemptStatus.SUCCEEDED)
        )
    ).scalar_one()

    failed_attempts = (
        await session.execute(
            select(func.count())
            .select_from(RecoveryAttempt)
            .where(RecoveryAttempt.status == RecoveryAttemptStatus.FAILED)
        )
    ).scalar_one()

    escalated_count = cases_by_status.get(RecoveryCaseStatus.ESCALATED.value, 0)
    stopped_count = cases_by_status.get(RecoveryCaseStatus.STOPPED.value, 0)

    outstanding_revenue = max(0, eligible_revenue - confirmed_recovered_revenue)
    recovery_rate = confirmed_recovered_revenue / eligible_revenue if eligible_revenue else 0.0
    average_recovery_amount = (
        confirmed_recovered_revenue / recovered_case_count if recovered_case_count else 0.0
    )
    escalation_rate = escalated_count / cases_total if cases_total else 0.0
    stop_rate = stopped_count / cases_total if cases_total else 0.0
    provider_failure_rate = failed_attempts / recovery_attempts if recovery_attempts else 0.0

    return RecoverySummaryRead(
        generated_at=datetime.now(UTC),
        cases_total=cases_total,
        cases_eligible=cases_eligible,
        cases_by_status=cases_by_status,
        total_revenue_at_risk=total_revenue_at_risk,
        eligible_revenue=eligible_revenue,
        confirmed_recovered_revenue=confirmed_recovered_revenue,
        outstanding_revenue=outstanding_revenue,
        recovery_rate=round(recovery_rate, 4),
        recovery_attempts=recovery_attempts,
        successful_payment_links_created=successful_links,
        average_recovery_amount=round(average_recovery_amount, 2),
        escalation_rate=round(escalation_rate, 4),
        stop_rate=round(stop_rate, 4),
        provider_failure_rate=round(provider_failure_rate, 4),
    )
