"""Proves scripts.seed_demo_batch actually demonstrates the Track 03
requirement — "measured money recovered across a batch, with compliant
escalation, stopping rules, and an audit trail" — through the real
pipeline (diagnose -> execute -> webhook -> aggregation), not through a
hand-typed number. See that module's docstring for the full rationale
and why FakeAIProvider/FakePaymentProvider are the only substitutions.
"""
from __future__ import annotations

from sqlalchemy import select

from app.domain.enums import RecoveryCaseStatus
from app.models.audit_event import AuditEvent
from app.services.recovery_summary_service import compute_recovery_summary
from scripts.seed_demo_batch import run_demo_batch


async def test_demo_batch_produces_the_expected_mixed_outcome(db_session):
    outcome = await run_demo_batch(db_session)

    assert len(outcome.case_ids) == 30

    by_status: dict[str, int] = {}
    for status in outcome.final_status_by_case.values():
        by_status[status.value] = by_status.get(status.value, 0) + 1

    # Every one of the four Track 03 outcomes must actually be produced —
    # not just recovered cases. A batch that only ever "succeeds" would
    # not demonstrate compliant escalation or stopping rules at all.
    assert by_status.get(RecoveryCaseStatus.RECOVERED.value) == 9
    assert by_status.get(RecoveryCaseStatus.FAILED.value) == 3
    assert by_status.get(RecoveryCaseStatus.STOPPED.value) == 11
    assert by_status.get(RecoveryCaseStatus.ESCALATED.value) == 7


async def test_demo_batch_measured_recovery_is_computed_from_real_confirmed_payments(db_session):
    await run_demo_batch(db_session)
    summary = await compute_recovery_summary(db_session)

    assert summary.cases_total == 30
    # 9 cases at 15,000 paise, confirmed via a simulated payment_link.paid
    # webhook — never counted merely from Payment Link creation.
    assert summary.confirmed_recovered_revenue == 9 * 15_000
    assert summary.successful_payment_links_created == 9 + 3  # paid + expired-but-created
    assert summary.recovery_rate == round(summary.confirmed_recovered_revenue / summary.eligible_revenue, 4)
    assert summary.escalation_rate > 0
    assert summary.stop_rate > 0


async def test_demo_batch_leaves_a_full_audit_trail_for_every_case(db_session):
    outcome = await run_demo_batch(db_session)

    for case_id in outcome.case_ids:
        stmt = select(AuditEvent).where(
            AuditEvent.entity_type == "recovery_case", AuditEvent.entity_id == case_id
        )
        events = (await db_session.execute(stmt)).scalars().all()
        assert len(events) > 0, f"case {case_id} has no audit trail"


async def test_demo_batch_recovered_case_audit_trail_tells_the_full_story(db_session):
    outcome = await run_demo_batch(db_session)
    recovered_case_id = next(
        case_id
        for case_id, status in outcome.final_status_by_case.items()
        if status == RecoveryCaseStatus.RECOVERED
    )

    stmt = (
        select(AuditEvent)
        .where(AuditEvent.entity_type == "recovery_case", AuditEvent.entity_id == recovered_case_id)
        .order_by(AuditEvent.created_at)
    )
    event_types = [row.event_type for row in (await db_session.execute(stmt)).scalars().all()]

    # The story the Track 03 bar asks for, end to end, in order.
    assert event_types.index("diagnosis_requested") < event_types.index("ai_diagnosis_created")
    assert event_types.index("ai_diagnosis_created") < event_types.index("recovery_recommendation_created")
    assert event_types.index("recovery_recommendation_created") < event_types.index("policy_evaluated")
    assert event_types.index("policy_evaluated") < event_types.index("execution_requested")
    assert event_types.index("execution_requested") < event_types.index("policy_rechecked")
    assert "payment_confirmed" in event_types
