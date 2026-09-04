"""Proves scripts.seed_demo_batch demonstrates the Track 03 requirement —
"measured money recovered across a batch, with compliant escalation,
stopping rules, and an audit trail" — through the real pipeline
(diagnose -> execute -> webhook -> aggregation), not a hand-typed number.

These tests inject `scripted_ai_service()` — a deterministic stand-in for
a live provider — so outcomes are exactly reproducible in CI. The
endpoint and CLI use the real provider; see `test_demo_api.py` and the
module docstring.
"""
from __future__ import annotations

from sqlalchemy import select

from app.domain.enums import RecoveryCaseStatus
from app.models.audit_event import AuditEvent
from app.services.recovery_summary_service import compute_recovery_summary
from scripts.seed_demo_batch import BATCH_SIZE, run_demo_batch, scripted_ai_service


async def test_demo_batch_produces_the_expected_mixed_outcome(db_session):
    outcome = await run_demo_batch(db_session, ai_service=scripted_ai_service())

    assert len(outcome.case_ids) == BATCH_SIZE == 30

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
    await run_demo_batch(db_session, ai_service=scripted_ai_service())
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
    outcome = await run_demo_batch(db_session, ai_service=scripted_ai_service())

    for case_id in outcome.case_ids:
        stmt = select(AuditEvent).where(
            AuditEvent.entity_type == "recovery_case", AuditEvent.entity_id == case_id
        )
        events = (await db_session.execute(stmt)).scalars().all()
        assert len(events) > 0, f"case {case_id} has no audit trail"


async def test_demo_batch_recovered_case_audit_trail_tells_the_full_story(db_session):
    outcome = await run_demo_batch(db_session, ai_service=scripted_ai_service())
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


async def test_demo_batch_reports_the_ai_model_it_used(db_session):
    outcome = await run_demo_batch(db_session, ai_service=scripted_ai_service())
    assert outcome.ai_model == "scripted-demo-stand-in"


async def test_demo_batch_with_no_ai_key_escalates_every_case_safely(db_session, monkeypatch):
    """The path a fresh clone hits: no GROQ_API_KEY -> the real
    AIRecommendationService falls back to ESCALATE for every case, and the
    batch still completes and still leaves a full audit trail — it just
    recovers nothing."""
    from app.ai.dependencies import get_ai_provider, get_ai_service
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "groq_api_key", "")
    get_ai_provider.cache_clear()  # so the unconfigured stand-in is built
    outcome = await run_demo_batch(db_session, ai_service=get_ai_service())

    statuses = set(outcome.final_status_by_case.values())
    assert statuses == {RecoveryCaseStatus.ESCALATED}

    summary = await compute_recovery_summary(db_session)
    assert summary.confirmed_recovered_revenue == 0
    assert summary.escalation_rate == 1.0
    get_ai_provider.cache_clear()
