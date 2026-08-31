from app.domain.enums import PolicyDecisionType, PolicyReasonCode, RecoveryAction, RecoveryCaseStatus
from app.domain.policy import PolicyConfig, PolicyEvaluationInput, evaluate_policy

CONFIG = PolicyConfig()


def _input(**overrides) -> PolicyEvaluationInput:
    defaults = dict(
        case_status=RecoveryCaseStatus.RECOMMENDED,
        proposed_action=RecoveryAction.RETRY_PAYMENT,
        attempt_number=1,
        days_since_discovery=2,
        customer_contact_count=0,
        recovery_confidence=0.8,
        amount=10_000,
    )
    defaults.update(overrides)
    return PolicyEvaluationInput(**defaults)


def test_allows_a_clean_retry():
    decision = evaluate_policy(_input(), CONFIG)
    assert decision.decision == PolicyDecisionType.ALLOW
    assert decision.reason_codes == []
    assert decision.policy_version == CONFIG.policy_version


def test_blocks_retry_beyond_max_retry_count():
    decision = evaluate_policy(_input(attempt_number=CONFIG.max_retry_count), CONFIG)
    assert decision.decision == PolicyDecisionType.BLOCK
    assert PolicyReasonCode.MAX_RETRIES_REACHED in decision.reason_codes


def test_blocks_action_outside_recovery_window():
    decision = evaluate_policy(
        _input(days_since_discovery=CONFIG.max_recovery_window_days + 1), CONFIG
    )
    assert PolicyReasonCode.RECOVERY_WINDOW_EXPIRED in decision.reason_codes


def test_blocks_contact_action_over_contact_cap():
    decision = evaluate_policy(
        _input(
            proposed_action=RecoveryAction.SEND_REMINDER,
            customer_contact_count=CONFIG.max_customer_contacts,
        ),
        CONFIG,
    )
    assert PolicyReasonCode.MAX_CONTACTS_REACHED in decision.reason_codes


def test_blocks_low_confidence_action():
    decision = evaluate_policy(
        _input(recovery_confidence=CONFIG.min_confidence_threshold - 0.01), CONFIG
    )
    assert PolicyReasonCode.CONFIDENCE_BELOW_THRESHOLD in decision.reason_codes


def test_high_value_payment_requires_higher_confidence():
    borderline_confidence = (
        CONFIG.min_confidence_threshold + CONFIG.high_value_min_confidence_threshold
    ) / 2
    decision = evaluate_policy(
        _input(amount=CONFIG.high_value_amount_threshold, recovery_confidence=borderline_confidence),
        CONFIG,
    )
    assert PolicyReasonCode.CONFIDENCE_BELOW_THRESHOLD in decision.reason_codes


def test_blocks_action_on_terminal_case():
    decision = evaluate_policy(_input(case_status=RecoveryCaseStatus.RECOVERED), CONFIG)
    assert decision.decision == PolicyDecisionType.BLOCK
    assert PolicyReasonCode.TERMINAL_STATE_PROTECTED in decision.reason_codes


def test_blocks_action_not_eligible_for_status():
    decision = evaluate_policy(
        _input(case_status=RecoveryCaseStatus.DISCOVERED, proposed_action=RecoveryAction.RETRY_PAYMENT),
        CONFIG,
    )
    assert PolicyReasonCode.ACTION_NOT_ELIGIBLE_FOR_STATUS in decision.reason_codes


def test_blocks_non_positive_amount():
    decision = evaluate_policy(_input(amount=0), CONFIG)
    assert PolicyReasonCode.AMOUNT_OUT_OF_BOUNDS in decision.reason_codes


def test_stop_and_escalate_are_never_blocked_by_expired_recovery_window():
    stale_days = CONFIG.max_recovery_window_days + 30
    stop_decision = evaluate_policy(
        _input(
            case_status=RecoveryCaseStatus.FAILED,
            proposed_action=RecoveryAction.STOP,
            days_since_discovery=stale_days,
        ),
        CONFIG,
    )
    assert stop_decision.decision == PolicyDecisionType.ALLOW

    escalate_decision = evaluate_policy(
        _input(
            case_status=RecoveryCaseStatus.FAILED,
            proposed_action=RecoveryAction.ESCALATE,
            days_since_discovery=stale_days,
        ),
        CONFIG,
    )
    assert escalate_decision.decision == PolicyDecisionType.ALLOW


def test_escalate_never_needs_confidence_check():
    decision = evaluate_policy(
        _input(
            case_status=RecoveryCaseStatus.DIAGNOSING,
            proposed_action=RecoveryAction.ESCALATE,
            recovery_confidence=0.0,
        ),
        CONFIG,
    )
    assert decision.decision == PolicyDecisionType.ALLOW


def test_collects_multiple_violations_at_once():
    decision = evaluate_policy(
        _input(
            case_status=RecoveryCaseStatus.RECOVERED,
            attempt_number=CONFIG.max_retry_count,
            amount=0,
        ),
        CONFIG,
    )
    assert PolicyReasonCode.TERMINAL_STATE_PROTECTED in decision.reason_codes
    assert PolicyReasonCode.MAX_RETRIES_REACHED in decision.reason_codes
    assert PolicyReasonCode.AMOUNT_OUT_OF_BOUNDS in decision.reason_codes
    assert len(decision.reason_codes) >= 3


def test_decision_is_deterministic():
    a = evaluate_policy(_input(), CONFIG)
    b = evaluate_policy(_input(), CONFIG)
    assert a == b
