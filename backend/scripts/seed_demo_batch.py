"""DEMO BATCH — NOT real Razorpay or Gemini results. Read this before using
the numbers this script prints or leaves in the database.

The Track 03 bar asks for "measured money recovered across a batch, with
compliant escalation, stopping rules, and an audit trail." In an
environment with no live Gemini/Razorpay credentials (this one, and any
judge's environment that hasn't configured its own), the recovery-summary
endpoint (`GET /api/v1/evaluation/recovery-summary`) has nothing to show —
zero cases exist, so every figure is honestly zero. That's correct
behavior, but it makes the batch-recovery requirement impossible to
*demonstrate* without either real credentials or this script.

What this script actually does: seeds a batch of realistic failed-payment
recovery cases, then runs *every one* through the real, unmodified
production pipeline —

    app.services.diagnosis_service.diagnose_recovery_case
    -> app.services.execution_service.execute_recovery_case
    -> app.payments.webhooks.parse_event + app.services.webhook_service.process_webhook_event
    -> app.services.recovery_summary_service.compute_recovery_summary

— with exactly two substitutions: `FakeAIProvider` stands in for Gemini,
and `FakePaymentProvider` stands in for Razorpay (both already exist,
already used throughout the automated test suite, never call a real API).
Every other line exercised — the state machine, the deterministic policy
engine, execution orchestration, webhook parsing, financial aggregation —
is the exact code that runs against real Gemini/Razorpay. The webhook
events themselves are hand-built (matching Razorpay's real payload shape)
and fed through the real parser + processor directly, bypassing only the
HTTP+signature layer (separately covered by test_webhook_workflow.py and
test_webhook_parsing.py) since there's no real HTTP delivery to sign here.

Every number this script prints or leaves in the database is the real
output of the real aggregation service run against real (if fake-I/O)
case records — nothing here is a hand-typed or invented metric. It is
also NOT the synthetic evaluation (`evaluation/`, 500+ cases scored
against a held-out ground truth) — that is a decision-quality/safety
harness with a completely different methodology; see
docs/razorpay-integration.md "Simulated vs. real evaluation."

NEVER present this batch's output as a real Razorpay Test Mode result.
It demonstrates that the pipeline correctly produces a measured recovery
rate, correctly stops/escalates cases it shouldn't act on, and leaves a
full audit trail — at batch scale, without needing live credentials.

Usage (from backend/, against a real DATABASE_URL — do not point this at
a database you care about; it inserts real rows):
    python -m scripts.seed_demo_batch
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.fake import FakeAIProvider
from app.ai.schemas import RecoveryRecommendation
from app.ai.service import AIRecommendationService
from app.domain.enums import (
    DiagnosisCategory,
    FailureReason,
    PaymentMethodType,
    PaymentStatus,
    RecoveryAction,
    RecoveryCaseStatus,
)
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.payments.providers.fake import FakePaymentProvider
from app.payments.webhooks import parse_event
from app.services import webhook_service
from app.services.diagnosis_service import diagnose_recovery_case
from app.services.execution_service import execute_recovery_case
from app.services.recovery_summary_service import compute_recovery_summary


@dataclass(frozen=True)
class DemoScenario:
    label: str
    count: int
    amount: int  # paise
    attempt_number: int
    customer_contact_count: int
    days_since_discovery: int
    failure_reason: FailureReason
    recommendation: RecoveryRecommendation
    # What to do once the case reaches EXECUTING with a live payment
    # request: simulate a full payment ("paid"), simulate expiry
    # ("expired"), or nothing further because the case already resolved
    # at the diagnosis step (None).
    post_execution: str | None


# Six real scenario shapes, deliberately mixed outcomes (not all
# "success") — a batch that recovers 100% of everything would not be a
# credible demonstration of "compliant escalation" and "stopping rules".
# Every threshold referenced below (0.55 confidence, 2 contacts, 14-day
# window) is the real app.domain.policy.PolicyConfig default.
_SCENARIOS: list[DemoScenario] = [
    DemoScenario(
        label="recoverable_and_confirmed_paid",
        count=9,
        amount=15_000,
        attempt_number=1,
        customer_contact_count=0,
        days_since_discovery=1,
        failure_reason=FailureReason.INSUFFICIENT_FUNDS,
        recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
            recovery_confidence=0.85,
            recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
            decision_explanation="Card declined for insufficient funds; a payment link gives the "
            "customer another chance to pay on their own schedule.",
        ),
        post_execution="paid",
    ),
    DemoScenario(
        label="recoverable_but_link_expires_unpaid",
        count=3,
        amount=8_000,
        attempt_number=1,
        customer_contact_count=0,
        days_since_discovery=2,
        failure_reason=FailureReason.INSUFFICIENT_FUNDS,
        recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
            recovery_confidence=0.80,
            recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
            decision_explanation="Card declined for insufficient funds; sending a payment link.",
        ),
        post_execution="expired",
    ),
    DemoScenario(
        label="policy_blocks_contact_cap",
        count=5,
        amount=12_000,
        attempt_number=2,
        customer_contact_count=2,  # >= PolicyConfig.max_customer_contacts (2)
        days_since_discovery=3,
        failure_reason=FailureReason.INSUFFICIENT_FUNDS,
        recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
            recovery_confidence=0.85,
            recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
            decision_explanation="Customer has already been contacted twice; recommending another "
            "payment link, but the policy engine has the final say on contact limits.",
        ),
        post_execution=None,  # policy BLOCKs -> STOPPED before any provider call
    ),
    DemoScenario(
        label="policy_blocks_low_confidence",
        count=5,
        amount=20_000,
        attempt_number=1,
        customer_contact_count=0,
        days_since_discovery=2,
        failure_reason=FailureReason.UNKNOWN,
        recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.UNKNOWN_FAILURE,
            recovery_confidence=0.30,  # < PolicyConfig.min_confidence_threshold (0.55)
            recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
            decision_explanation="Failure reason is unclear; recommending a payment link with low "
            "confidence — the policy engine will not permit acting on it as-is.",
        ),
        post_execution=None,  # policy BLOCKs -> ESCALATED before any provider call
    ),
    DemoScenario(
        label="ai_recommends_stop",
        count=3,
        amount=6_000,
        attempt_number=5,
        customer_contact_count=1,
        days_since_discovery=10,
        failure_reason=FailureReason.AUTHENTICATION_FAILED,
        recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.REPEATED_FAILURE,
            recovery_confidence=0.10,
            recommended_action=RecoveryAction.STOP,
            decision_explanation="Five failed attempts with a poor historical success rate; further "
            "recovery attempts are not worthwhile.",
        ),
        post_execution=None,
    ),
    DemoScenario(
        label="ai_recommends_escalate",
        count=2,
        amount=45_000,
        attempt_number=1,
        customer_contact_count=0,
        days_since_discovery=1,
        failure_reason=FailureReason.UNKNOWN,
        recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.UNKNOWN_FAILURE,
            recovery_confidence=0.0,
            recommended_action=RecoveryAction.ESCALATE,
            decision_explanation="Failure reason and payment context don't fit a known pattern; "
            "this needs a human to look at it.",
        ),
        post_execution=None,
    ),
    DemoScenario(
        label="policy_blocks_recovery_window_expired",
        count=3,
        amount=10_000,
        attempt_number=1,
        customer_contact_count=0,
        days_since_discovery=20,  # > PolicyConfig.max_recovery_window_days (14)
        failure_reason=FailureReason.EXPIRED_INSTRUMENT,
        recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
            recovery_confidence=0.85,
            recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
            decision_explanation="Card expired; recommending a payment link, though this case is "
            "old enough that the policy engine may have moved on from it.",
        ),
        post_execution=None,  # policy BLOCKs -> STOPPED before any provider call
    ),
]


@dataclass(frozen=True)
class DemoBatchOutcome:
    case_ids: list[uuid.UUID]
    final_status_by_case: dict[uuid.UUID, RecoveryCaseStatus]


async def _seed_case(session: AsyncSession, scenario: DemoScenario, index: int) -> uuid.UUID:
    customer = Customer(total_payments_count=5, total_failed_payments_count=1)
    session.add(customer)
    await session.flush()

    created_at = datetime.now(UTC) - timedelta(days=scenario.days_since_discovery)
    payment = Payment(
        customer_id=customer.id,
        amount=scenario.amount,
        currency="INR",
        status=PaymentStatus.FAILED,
        payment_method_type=PaymentMethodType.CARD,
        failure_reason=scenario.failure_reason,
        attempt_number=scenario.attempt_number,
        created_at=created_at,
    )
    session.add(payment)
    await session.flush()

    case = RecoveryCase(
        payment_id=payment.id,
        status=RecoveryCaseStatus.DISCOVERED,
        revenue_at_risk=scenario.amount,
        current_attempt_number=0,
        customer_contact_count=scenario.customer_contact_count,
        created_at=created_at,
    )
    session.add(case)
    await session.flush()
    await session.commit()
    return case.id


async def run_demo_batch(session: AsyncSession) -> DemoBatchOutcome:
    """Seeds and fully processes the demo batch. Returns each case's final
    status so a caller (this module's __main__, or a test) can verify the
    real pipeline produced the expected mix of outcomes."""
    payment_provider = FakePaymentProvider()
    case_ids: list[uuid.UUID] = []
    final_status_by_case: dict[uuid.UUID, RecoveryCaseStatus] = {}

    case_index = 0
    for scenario in _SCENARIOS:
        for _ in range(scenario.count):
            case_index += 1
            correlation_id = f"demo-batch-{case_index:03d}-{scenario.label}"
            case_id = await _seed_case(session, scenario, case_index)
            case_ids.append(case_id)

            ai_provider = FakeAIProvider(recommendation=scenario.recommendation)
            ai_service = AIRecommendationService(ai_provider, model_name="demo-batch-fake")

            diagnosis = await diagnose_recovery_case(
                session, case_id, ai_service=ai_service, correlation_id=correlation_id
            )

            if diagnosis.case_status != RecoveryCaseStatus.APPROVED:
                final_status_by_case[case_id] = diagnosis.case_status
                continue

            execution = await execute_recovery_case(
                session, case_id, provider=payment_provider, correlation_id=correlation_id
            )

            if not execution.executed or scenario.post_execution is None:
                final_status_by_case[case_id] = execution.case_status
                continue

            provider_reference = execution.provider_reference
            assert provider_reference is not None  # guaranteed by execution.executed being True

            if scenario.post_execution == "paid":
                payload = {
                    "event": "payment_link.paid",
                    "payload": {
                        "payment_link": {
                            "entity": {
                                "id": provider_reference,
                                "status": "paid",
                                "amount_paid": execution.amount,
                            }
                        },
                        "payment": {"entity": {"id": f"pay_fake_{uuid.uuid4().hex[:14]}"}},
                    },
                }
            else:  # "expired"
                payload = {
                    "event": "payment_link.expired",
                    "payload": {
                        "payment_link": {
                            "entity": {"id": provider_reference, "status": "expired", "amount_paid": 0}
                        }
                    },
                }

            event = parse_event(payload)
            await webhook_service.process_webhook_event(session, event, correlation_id=correlation_id)

            # Re-read the case's final status after the webhook was processed.
            final_case = await session.get(RecoveryCase, case_id)
            assert final_case is not None
            final_status_by_case[case_id] = RecoveryCaseStatus(final_case.status)

    return DemoBatchOutcome(case_ids=case_ids, final_status_by_case=final_status_by_case)


async def _main() -> None:
    # Imported here, not at module scope: __main__ needs a real DB
    # session; importing this at module scope would make `run_demo_batch`
    # unimportable from a test without also constructing a real engine.
    from app.db.session import AsyncSessionLocal

    print("=" * 78)
    print("DEMO BATCH - FakeAIProvider + FakePaymentProvider. NOT Gemini, NOT Razorpay.")
    print("See this file's module docstring before using or presenting these numbers.")
    print("=" * 78)

    async with AsyncSessionLocal() as session:
        outcome = await run_demo_batch(session)
        summary = await compute_recovery_summary(session)

    print(f"\nCases processed: {len(outcome.case_ids)}")
    by_status: dict[str, int] = {}
    for status in outcome.final_status_by_case.values():
        by_status[status.value] = by_status.get(status.value, 0) + 1
    for status_name, count in sorted(by_status.items()):
        print(f"  {status_name}: {count}")

    print("\nMeasured recovery summary (real aggregation, real DB rows, fake I/O):")
    print(f"  eligible_revenue:            {summary.eligible_revenue} paise")
    print(f"  confirmed_recovered_revenue: {summary.confirmed_recovered_revenue} paise")
    print(f"  recovery_rate:               {summary.recovery_rate:.2%}")
    print(f"  escalation_rate:             {summary.escalation_rate:.2%}")
    print(f"  stop_rate:                   {summary.stop_rate:.2%}")
    print(f"  recovery_attempts:           {summary.recovery_attempts}")
    print(f"  successful_payment_links_created: {summary.successful_payment_links_created}")
    print(
        "\nThese rows are now in your database - GET /api/v1/evaluation/recovery-summary "
        "and the frontend Overview page will show them too."
    )
    print("Again: this is a FakeAIProvider/FakePaymentProvider demo batch, not a real result.")


if __name__ == "__main__":
    asyncio.run(_main())
