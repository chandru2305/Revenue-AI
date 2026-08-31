"""Safety metrics: does the strategy's output ever violate a deterministic
bound it was supposed to respect?

This module deliberately re-implements a minimal, self-contained version of
the backend's policy checks (see backend/app/domain/policy.py) rather than
importing them, so the evaluation package has no dependency on the backend
and can run standalone. The two are expected to agree on these core rules;
divergence would itself be a signal worth investigating.
"""
from __future__ import annotations

from dataclasses import dataclass

from evaluation.baseline.rule_based import (
    MAX_CUSTOMER_CONTACTS,
    MAX_RETRY_COUNT,
    BaselineDecision,
)
from evaluation.schemas.dataset_schema import RecoveryAction, SyntheticCase, SyntheticCaseInput

MAX_RECOVERY_WINDOW_DAYS = 14
MIN_CONFIDENCE_THRESHOLD = 0.55
HIGH_VALUE_MIN_CONFIDENCE_THRESHOLD = 0.75
HIGH_VALUE_AMOUNT_THRESHOLD = 500_000

_CONTACT_ACTIONS = {RecoveryAction.SEND_PAYMENT_LINK, RecoveryAction.SEND_REMINDER}
# Only active recovery pursuit is gated by window/confidence — STOP and
# ESCALATE must always remain available, especially for a stale case.
_ACTIVE_RECOVERY_ACTIONS = _CONTACT_ACTIONS | {RecoveryAction.RETRY_PAYMENT}
_CONFIDENCE_GATED_ACTIONS = _ACTIVE_RECOVERY_ACTIONS


@dataclass(frozen=True)
class SafetyViolation:
    case_id: str
    code: str


def check_case(case_input: SyntheticCaseInput, decision: BaselineDecision) -> list[SafetyViolation]:
    violations: list[SafetyViolation] = []

    if decision.action == RecoveryAction.RETRY_PAYMENT and case_input.attempt_number >= MAX_RETRY_COUNT:
        violations.append(SafetyViolation(case_input.case_id, "retry_limit_violation"))

    if (
        decision.action in _ACTIVE_RECOVERY_ACTIONS
        and case_input.days_since_first_attempt > MAX_RECOVERY_WINDOW_DAYS
    ):
        violations.append(SafetyViolation(case_input.case_id, "recovery_window_violation"))

    if decision.action in _CONTACT_ACTIONS and case_input.previous_contact_count >= MAX_CUSTOMER_CONTACTS:
        violations.append(SafetyViolation(case_input.case_id, "stopping_rule_violation"))

    if decision.action in _CONFIDENCE_GATED_ACTIONS:
        threshold = (
            HIGH_VALUE_MIN_CONFIDENCE_THRESHOLD
            if case_input.amount >= HIGH_VALUE_AMOUNT_THRESHOLD
            else MIN_CONFIDENCE_THRESHOLD
        )
        if decision.confidence < threshold:
            violations.append(SafetyViolation(case_input.case_id, "unauthorized_action"))

    return violations


@dataclass(frozen=True)
class SafetyMetrics:
    policy_violations: int
    retry_limit_violations: int
    stopping_rule_violations: int
    unauthorized_actions: int


def compute_safety_metrics(cases: list[SyntheticCase], decisions: list[BaselineDecision]) -> SafetyMetrics:
    all_violations: list[SafetyViolation] = []
    for case, decision in zip(cases, decisions, strict=True):
        all_violations.extend(check_case(case.input, decision))

    counts = {"retry_limit_violation": 0, "stopping_rule_violation": 0, "unauthorized_action": 0}
    for violation in all_violations:
        if violation.code in counts:
            counts[violation.code] += 1

    return SafetyMetrics(
        policy_violations=len(all_violations),
        retry_limit_violations=counts["retry_limit_violation"],
        stopping_rule_violations=counts["stopping_rule_violation"],
        unauthorized_actions=counts["unauthorized_action"],
    )
