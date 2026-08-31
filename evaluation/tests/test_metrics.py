from evaluation.baseline.rule_based import BaselineDecision
from evaluation.metrics.decision import compute_decision_metrics
from evaluation.metrics.financial import compute_financial_metrics
from evaluation.metrics.operational import run_and_measure
from evaluation.metrics.safety import compute_safety_metrics
from evaluation.schemas.dataset_schema import (
    FailureReason,
    GroundTruth,
    PaymentMethodType,
    RecoveryAction,
    ScenarioType,
    SyntheticCase,
    SyntheticCaseInput,
)


def _case(
    case_id: str, amount: int, expected_action: RecoveryAction, recoverable: bool, **overrides
) -> SyntheticCase:
    defaults = dict(
        case_id=case_id,
        scenario_type=ScenarioType.TEMPORARY_FAILURE,
        amount=amount,
        payment_method_type=PaymentMethodType.CARD,
        failure_reason=FailureReason.NETWORK_ERROR,
        attempt_number=1,
        days_since_first_attempt=0,
        previous_contact_count=0,
        customer_payment_history_success_rate=0.8,
        is_high_value=False,
    )
    defaults.update(overrides)
    case_input = SyntheticCaseInput(**defaults)
    ground_truth = GroundTruth(expected_action=expected_action, recoverable=recoverable, rationale="test")
    return SyntheticCase(input=case_input, ground_truth=ground_truth)


def test_financial_and_decision_metrics_on_a_known_fixture():
    cases = [
        _case("c1", 1000, RecoveryAction.RETRY_PAYMENT, recoverable=True),
        _case("c2", 2000, RecoveryAction.RETRY_PAYMENT, recoverable=True),
        _case("c3", 3000, RecoveryAction.STOP, recoverable=False),
        _case("c4", 4000, RecoveryAction.ESCALATE, recoverable=False),
    ]
    decisions = [
        BaselineDecision(RecoveryAction.RETRY_PAYMENT, 0.9, "matches"),  # c1: correct, recoverable
        BaselineDecision(RecoveryAction.SEND_PAYMENT_LINK, 0.9, "wrong action"),  # c2: active, wrong
        BaselineDecision(RecoveryAction.STOP, 0.9, "matches"),  # c3: correct, safe
        BaselineDecision(RecoveryAction.RETRY_PAYMENT, 0.9, "should have escalated"),  # c4: unsafe overreach
    ]

    financial = compute_financial_metrics(cases, decisions)
    assert financial.total_revenue_at_risk == 10_000
    assert financial.eligible_revenue == 1000 + 2000 + 4000  # active-intervention decisions
    assert financial.recovered_revenue == 1000  # only c1 matches ground truth AND is recoverable
    assert financial.recovery_rate == round(1000 / 7000, 4)

    decision_metrics = compute_decision_metrics(cases, decisions)
    assert decision_metrics.intervention_accuracy == 0.5  # c1, c3 correct out of 4
    assert decision_metrics.appropriate_escalation_rate == 0.0  # c4 needed escalate, got retry
    assert decision_metrics.inappropriate_intervention_rate == 0.5  # 1 of 2 (c3, c4) wrongly acted


def test_safety_metrics_catch_a_retry_limit_violation():
    case = _case("stale", 1000, RecoveryAction.STOP, recoverable=False, attempt_number=5)
    # A (deliberately wrong) decision that retries a payment past the retry limit.
    bad_decision = BaselineDecision(RecoveryAction.RETRY_PAYMENT, 0.9, "buggy strategy")

    metrics = compute_safety_metrics([case], [bad_decision])
    assert metrics.retry_limit_violations == 1
    assert metrics.policy_violations >= 1


def test_safety_metrics_are_clean_for_well_behaved_decisions():
    case = _case("ok", 1000, RecoveryAction.RETRY_PAYMENT, recoverable=True)
    good_decision = BaselineDecision(RecoveryAction.RETRY_PAYMENT, 0.9, "confident retry")

    metrics = compute_safety_metrics([case], [good_decision])
    assert metrics.policy_violations == 0


def test_operational_metrics_measure_a_real_run():
    cases = [_case(f"c{i}", 1000, RecoveryAction.RETRY_PAYMENT, recoverable=True) for i in range(10)]
    decisions, metrics = run_and_measure(cases, lambda case: BaselineDecision(RecoveryAction.STOP, 0.9, "x"))
    assert len(decisions) == 10
    assert metrics.cases_processed == 10
    assert metrics.average_processing_time_ms >= 0
    assert metrics.throughput_per_second > 0
