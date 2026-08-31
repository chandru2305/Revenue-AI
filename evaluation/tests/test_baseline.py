from evaluation.baseline.rule_based import MAX_CUSTOMER_CONTACTS, MAX_RETRY_COUNT, decide
from evaluation.schemas.dataset_schema import (
    FailureReason,
    PaymentMethodType,
    RecoveryAction,
    ScenarioType,
    SyntheticCaseInput,
)


def _case(**overrides) -> SyntheticCaseInput:
    defaults = dict(
        case_id="test_case",
        scenario_type=ScenarioType.TEMPORARY_FAILURE,
        amount=10_000,
        payment_method_type=PaymentMethodType.CARD,
        failure_reason=FailureReason.NETWORK_ERROR,
        attempt_number=1,
        days_since_first_attempt=0,
        previous_contact_count=0,
        customer_payment_history_success_rate=0.8,
        is_high_value=False,
    )
    defaults.update(overrides)
    return SyntheticCaseInput(**defaults)


def test_temporary_failure_recommends_retry():
    decision = decide(_case(failure_reason=FailureReason.NETWORK_ERROR))
    assert decision.action == RecoveryAction.RETRY_PAYMENT


def test_customer_side_failure_recommends_payment_link():
    decision = decide(_case(failure_reason=FailureReason.INSUFFICIENT_FUNDS))
    assert decision.action == RecoveryAction.SEND_PAYMENT_LINK


def test_unknown_failure_escalates():
    decision = decide(_case(failure_reason=FailureReason.UNKNOWN))
    assert decision.action == RecoveryAction.ESCALATE


def test_contact_cap_forces_stop_regardless_of_failure_reason():
    decision = decide(_case(previous_contact_count=MAX_CUSTOMER_CONTACTS))
    assert decision.action == RecoveryAction.STOP


def test_retry_limit_with_low_success_rate_stops():
    decision = decide(
        _case(attempt_number=MAX_RETRY_COUNT, customer_payment_history_success_rate=0.1)
    )
    assert decision.action == RecoveryAction.STOP


def test_retry_limit_with_decent_success_rate_escalates():
    decision = decide(
        _case(attempt_number=MAX_RETRY_COUNT, customer_payment_history_success_rate=0.5)
    )
    assert decision.action == RecoveryAction.ESCALATE


def test_confidence_is_always_between_zero_and_one():
    for reason in FailureReason:
        decision = decide(_case(failure_reason=reason))
        assert 0.0 <= decision.confidence <= 1.0


def test_decision_is_deterministic_for_same_input():
    a = decide(_case())
    b = decide(_case())
    assert a == b
