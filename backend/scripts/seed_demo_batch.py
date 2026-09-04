"""Recovery batch — a curated spread of failed payments run end to end so
the Track 03 bar ("measured money recovered across a batch, with compliant
escalation, stopping rules, and an audit trail") is demonstrable on a
fresh database.

## What is real

- **The case inputs.** Seven scenario shapes with realistic field
  relationships — attempt counts, contact counts, ageing, amounts,
  failure reasons — chosen so the batch exercises every Track 03 outcome
  (recovered, failed, stopped, escalated), not just success.
- **The AI diagnosis.** `run_demo_batch` takes an `ai_service` and calls
  it for real. The endpoint and CLI pass the live one — with `GROQ_API_KEY`
  set, every case gets a real `openai/gpt-oss-120b` diagnosis; with no
  key, the safe-fallback ESCALATE path runs, exactly as in production.
- **The deterministic core.** State machine, `evaluate_policy`, execution
  orchestration, optimistic locking, the append-only audit trail — the
  exact code paths the individual API endpoints use, unchanged.
- **The recovery-rate arithmetic.** `compute_recovery_summary` aggregates
  the rows this batch produced. Nothing here is a hand-typed metric.

## What is simulated, and why it has to be

The Razorpay Test Mode API is not configured in this environment, so the
one external write — `create_payment_link` — is served by
`FakePaymentProvider`, and the `payment_link.paid` / `payment_link.expired`
webhooks are hand-built (matching Razorpay's real payload shape) and fed
through the real `parse_event` + `webhook_service` path, bypassing only
the HTTP + signature layer (covered separately by
`test_webhook_workflow.py` / `test_webhook_parsing.py`).

This is the only way to reach the `RECOVERED` state without a live
gateway. **Never present the recovered-revenue figure as a real Razorpay
result** — it is a real measurement over real rows whose payment
confirmation was simulated.

Usage (from `backend/`, against a throwaway `DATABASE_URL` — it inserts
real rows):

    python -m scripts.seed_demo_batch
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context import PaymentRecoveryContext
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
from app.domain.providers.base import PaymentProvider
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
    amount: int  # paise — unique per scenario, used to route the scripted AI stand-in
    attempt_number: int
    customer_contact_count: int
    days_since_discovery: int
    failure_reason: FailureReason
    # Used ONLY by `scripted_ai_service()` (tests, offline demos). The
    # endpoint and CLI ignore this and call the real provider.
    scripted_recommendation: RecoveryRecommendation
    # If a case actually reaches EXECUTING (real diagnosis + policy ALLOW +
    # an executable action), simulate this webhook. `None` means the case
    # is expected to resolve at diagnosis and no execution/webhook happens.
    simulate_webhook: str | None


# Every threshold referenced below (0.55 confidence, 2 contacts, 14-day
# window) is the real app.domain.policy.PolicyConfig default. The scenario
# INPUTS drive the deterministic policy demonstrations regardless of what
# the model recommends; scripted_recommendation only matters when the
# scripted AI stand-in is used instead of a live provider.
_SCENARIOS: list[DemoScenario] = [
    DemoScenario(
        label="recoverable_and_confirmed_paid",
        count=9,
        amount=15_000,
        attempt_number=1,
        customer_contact_count=0,
        days_since_discovery=1,
        failure_reason=FailureReason.INSUFFICIENT_FUNDS,
        scripted_recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
            recovery_confidence=0.85,
            recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
            decision_explanation="Card declined for insufficient funds; a payment link gives the "
            "customer another chance to pay on their own schedule.",
        ),
        simulate_webhook="paid",
    ),
    DemoScenario(
        label="recoverable_but_link_expires_unpaid",
        count=3,
        amount=8_000,
        attempt_number=1,
        customer_contact_count=0,
        days_since_discovery=2,
        failure_reason=FailureReason.INSUFFICIENT_FUNDS,
        scripted_recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
            recovery_confidence=0.80,
            recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
            decision_explanation="Card declined for insufficient funds; sending a payment link.",
        ),
        simulate_webhook="expired",
    ),
    DemoScenario(
        label="policy_blocks_contact_cap",
        count=5,
        amount=12_000,
        attempt_number=2,
        customer_contact_count=2,  # >= PolicyConfig.max_customer_contacts (2)
        days_since_discovery=3,
        failure_reason=FailureReason.INSUFFICIENT_FUNDS,
        scripted_recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
            recovery_confidence=0.85,
            recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
            decision_explanation="Customer has already been contacted twice; recommending another "
            "payment link, but the policy engine has the final say on contact limits.",
        ),
        simulate_webhook=None,  # policy BLOCKs -> STOPPED before any provider call
    ),
    DemoScenario(
        label="policy_blocks_low_confidence",
        count=5,
        amount=20_000,
        attempt_number=1,
        customer_contact_count=0,
        days_since_discovery=2,
        failure_reason=FailureReason.UNKNOWN,
        scripted_recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.UNKNOWN_FAILURE,
            recovery_confidence=0.30,  # < PolicyConfig.min_confidence_threshold (0.55)
            recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
            decision_explanation="Failure reason is unclear; recommending a payment link with low "
            "confidence — the policy engine will not permit acting on it as-is.",
        ),
        simulate_webhook=None,  # policy BLOCKs -> ESCALATED before any provider call
    ),
    DemoScenario(
        label="ai_recommends_stop",
        count=3,
        amount=6_000,
        attempt_number=5,
        customer_contact_count=1,
        days_since_discovery=10,
        failure_reason=FailureReason.AUTHENTICATION_FAILED,
        scripted_recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.REPEATED_FAILURE,
            recovery_confidence=0.10,
            recommended_action=RecoveryAction.STOP,
            decision_explanation="Five failed attempts with a poor historical success rate; further "
            "recovery attempts are not worthwhile.",
        ),
        simulate_webhook=None,
    ),
    DemoScenario(
        label="ai_recommends_escalate",
        count=2,
        amount=45_000,
        attempt_number=1,
        customer_contact_count=0,
        days_since_discovery=1,
        failure_reason=FailureReason.UNKNOWN,
        scripted_recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.UNKNOWN_FAILURE,
            recovery_confidence=0.0,
            recommended_action=RecoveryAction.ESCALATE,
            decision_explanation="Failure reason and payment context don't fit a known pattern; "
            "this needs a human to look at it.",
        ),
        simulate_webhook=None,
    ),
    DemoScenario(
        label="policy_blocks_recovery_window_expired",
        count=3,
        amount=10_000,
        attempt_number=1,
        customer_contact_count=0,
        days_since_discovery=20,  # > PolicyConfig.max_recovery_window_days (14)
        failure_reason=FailureReason.EXPIRED_INSTRUMENT,
        scripted_recommendation=RecoveryRecommendation(
            diagnosis_category=DiagnosisCategory.CUSTOMER_SIDE_FAILURE,
            recovery_confidence=0.85,
            recommended_action=RecoveryAction.SEND_PAYMENT_LINK,
            decision_explanation="Card expired; recommending a payment link, though this case is "
            "old enough that the policy engine may have moved on from it.",
        ),
        simulate_webhook=None,  # policy BLOCKs -> STOPPED before any provider call
    ),
]

BATCH_SIZE = sum(s.count for s in _SCENARIOS)


def scripted_ai_service() -> AIRecommendationService:
    """A deterministic stand-in for a live provider, for tests and offline
    demos. Routes each case to its scenario's `scripted_recommendation` by
    the scenario's unique `amount` — so a varied batch gets varied output
    without a network call, and outcomes are exactly reproducible.
    """
    by_amount = {s.amount: s.scripted_recommendation for s in _SCENARIOS}

    def _recommend(context: PaymentRecoveryContext) -> RecoveryRecommendation:
        return by_amount[context.amount]

    return AIRecommendationService(
        FakeAIProvider(recommend_fn=_recommend), model_name="scripted-demo-stand-in"
    )


@dataclass(frozen=True)
class DemoBatchOutcome:
    case_ids: list[uuid.UUID]
    final_status_by_case: dict[uuid.UUID, RecoveryCaseStatus]
    ai_model: str


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


async def run_demo_batch(
    session: AsyncSession,
    *,
    ai_service: AIRecommendationService,
    payment_provider: PaymentProvider | None = None,
) -> DemoBatchOutcome:
    """Seed the batch, then run every case through the real pipeline with
    the given `ai_service`. `payment_provider` defaults to
    `FakePaymentProvider` — the Razorpay stand-in that lets a case reach
    `RECOVERED` without a live gateway (see module docstring).
    """
    provider = payment_provider or FakePaymentProvider()
    case_ids: list[uuid.UUID] = []
    final_status_by_case: dict[uuid.UUID, RecoveryCaseStatus] = {}

    case_index = 0
    for scenario in _SCENARIOS:
        for _ in range(scenario.count):
            case_index += 1
            correlation_id = f"demo-batch-{case_index:03d}-{scenario.label}"
            case_id = await _seed_case(session, scenario, case_index)
            case_ids.append(case_id)

            diagnosis = await diagnose_recovery_case(
                session, case_id, ai_service=ai_service, correlation_id=correlation_id
            )

            if diagnosis.case_status != RecoveryCaseStatus.APPROVED:
                final_status_by_case[case_id] = diagnosis.case_status
                continue

            execution = await execute_recovery_case(
                session, case_id, provider=provider, correlation_id=correlation_id
            )

            if not execution.executed or scenario.simulate_webhook is None:
                final_status_by_case[case_id] = execution.case_status
                continue

            provider_reference = execution.provider_reference
            assert provider_reference is not None  # guaranteed by execution.executed being True

            if scenario.simulate_webhook == "paid":
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
                        "payment": {"entity": {"id": f"pay_sim_{uuid.uuid4().hex[:14]}"}},
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

            final_case = await session.get(RecoveryCase, case_id)
            assert final_case is not None
            final_status_by_case[case_id] = RecoveryCaseStatus(final_case.status)

    return DemoBatchOutcome(
        case_ids=case_ids,
        final_status_by_case=final_status_by_case,
        ai_model=ai_service._model_name,
    )


async def _main() -> None:
    # Imported here, not at module scope: __main__ needs a real DB session
    # and the live provider wiring; importing those at module scope would
    # make `run_demo_batch` unimportable from a test without a real engine.
    from app.ai.dependencies import get_ai_service
    from app.db.session import AsyncSessionLocal

    ai_service = get_ai_service()

    print("=" * 78)
    print("RECOVERY BATCH")
    print(f"  AI diagnosis:  LIVE ({ai_service._model_name}) — real calls, real reasoning")
    print("  Execution + webhook:  SIMULATED (FakePaymentProvider — Razorpay not configured)")
    print("  Everything else (policy, state machine, aggregation, audit):  real")
    print("=" * 78)

    async with AsyncSessionLocal() as session:
        outcome = await run_demo_batch(session, ai_service=ai_service)
        summary = await compute_recovery_summary(session)

    print(f"\nCases processed: {len(outcome.case_ids)}")
    by_status: dict[str, int] = {}
    for status in outcome.final_status_by_case.values():
        by_status[status.value] = by_status.get(status.value, 0) + 1
    for status_name, count in sorted(by_status.items()):
        print(f"  {status_name}: {count}")

    print("\nMeasured recovery summary (real aggregation over real DB rows):")
    print(f"  eligible_revenue:            {summary.eligible_revenue} paise")
    print(f"  confirmed_recovered_revenue: {summary.confirmed_recovered_revenue} paise")
    print(f"  recovery_rate:               {summary.recovery_rate:.2%}")
    print(f"  escalation_rate:             {summary.escalation_rate:.2%}")
    print(f"  stop_rate:                   {summary.stop_rate:.2%}")
    print(f"  recovery_attempts:           {summary.recovery_attempts}")
    print(f"  successful_payment_links_created: {summary.successful_payment_links_created}")
    print(
        "\nThese rows are now in your database — GET /api/v1/evaluation/recovery-summary "
        "and the frontend Overview page will show them too."
    )
    print(
        "The recovered figure's payment confirmation was simulated "
        "(no Razorpay) — do not quote it as a Razorpay Test Mode result."
    )


if __name__ == "__main__":
    asyncio.run(_main())
