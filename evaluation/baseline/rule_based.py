"""Deterministic rule-based baseline recovery strategy.

This is NOT meant to be clever. Its purpose is to give the evaluation
framework a simple, explainable non-AI reference point so that later
AI-assisted strategies can be judged against something better than "no
comparison at all." See docs/evaluation-methodology.md.

Written independently of `evaluation.generators.scenarios` ground truth —
it only ever sees `SyntheticCaseInput`, never `GroundTruth`.
"""
from __future__ import annotations

from dataclasses import dataclass

from evaluation.schemas.dataset_schema import FailureReason, RecoveryAction, SyntheticCaseInput

MAX_RETRY_COUNT = 3
MAX_CUSTOMER_CONTACTS = 2
LOW_RECOVERY_LIKELIHOOD_THRESHOLD = 0.3

_TEMPORARY_FAILURE_REASONS = {
    FailureReason.NETWORK_ERROR,
    FailureReason.GATEWAY_TIMEOUT,
    FailureReason.PROVIDER_ERROR,
}
_CUSTOMER_SIDE_FAILURE_REASONS = {
    FailureReason.INSUFFICIENT_FUNDS,
    FailureReason.EXPIRED_INSTRUMENT,
    FailureReason.AUTHENTICATION_FAILED,
}


@dataclass(frozen=True)
class BaselineDecision:
    action: RecoveryAction
    confidence: float
    rationale: str


def decide(case_input: SyntheticCaseInput) -> BaselineDecision:
    if case_input.previous_contact_count >= MAX_CUSTOMER_CONTACTS:
        return BaselineDecision(RecoveryAction.STOP, 0.9, "Customer contact cap reached.")

    if case_input.attempt_number >= MAX_RETRY_COUNT:
        if case_input.customer_payment_history_success_rate >= LOW_RECOVERY_LIKELIHOOD_THRESHOLD:
            return BaselineDecision(
                RecoveryAction.ESCALATE, 0.5, "Retry limit reached but recovery signal is non-trivial."
            )
        return BaselineDecision(
            RecoveryAction.STOP, 0.85, "Retry limit reached and recovery likelihood is very low."
        )

    if case_input.failure_reason == FailureReason.UNKNOWN:
        return BaselineDecision(RecoveryAction.ESCALATE, 0.4, "Failure cause unknown; escalate.")

    if case_input.failure_reason in _TEMPORARY_FAILURE_REASONS:
        confidence = min(0.95, 0.6 + case_input.customer_payment_history_success_rate * 0.3)
        return BaselineDecision(RecoveryAction.RETRY_PAYMENT, round(confidence, 2), "Transient failure.")

    if case_input.failure_reason in _CUSTOMER_SIDE_FAILURE_REASONS:
        confidence = min(0.9, 0.5 + case_input.customer_payment_history_success_rate * 0.3)
        return BaselineDecision(
            RecoveryAction.SEND_PAYMENT_LINK, round(confidence, 2), "Instrument-side failure."
        )

    return BaselineDecision(RecoveryAction.ESCALATE, 0.3, "Unclassified failure reason; escalate.")
