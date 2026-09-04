"""Deterministic safety-policy engine.

This module is the enforcement boundary between AI recommendations and any
action that touches money or contacts a customer. It is intentionally pure
and dependency-free: same input always produces the same decision, and
nothing here ever calls an LLM. The AI may propose; only this module (and
the state machine it works alongside) may authorize.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from app.domain.enums import (
    TERMINAL_RECOVERY_CASE_STATUSES,
    PolicyDecisionType,
    PolicyReasonCode,
    RecoveryAction,
    RecoveryCaseStatus,
)

POLICY_VERSION = "v1"

# Which actions are even meaningful while a case sits in a given status.
# Anything not listed here is implicitly not eligible.
_ACTION_ELIGIBLE_STATUSES: dict[RecoveryAction, frozenset[RecoveryCaseStatus]] = {
    RecoveryAction.RETRY_PAYMENT: frozenset(
        {RecoveryCaseStatus.RECOMMENDED, RecoveryCaseStatus.POLICY_REVIEW, RecoveryCaseStatus.APPROVED}
    ),
    RecoveryAction.SEND_PAYMENT_LINK: frozenset(
        {RecoveryCaseStatus.RECOMMENDED, RecoveryCaseStatus.POLICY_REVIEW, RecoveryCaseStatus.APPROVED}
    ),
    RecoveryAction.SEND_REMINDER: frozenset(
        {RecoveryCaseStatus.RECOMMENDED, RecoveryCaseStatus.POLICY_REVIEW, RecoveryCaseStatus.APPROVED}
    ),
    RecoveryAction.ESCALATE: frozenset(
        {
            RecoveryCaseStatus.DIAGNOSING,
            RecoveryCaseStatus.RECOMMENDED,
            RecoveryCaseStatus.POLICY_REVIEW,
            RecoveryCaseStatus.EXECUTING,
            RecoveryCaseStatus.FAILED,
        }
    ),
    RecoveryAction.STOP: frozenset(
        {
            RecoveryCaseStatus.ELIGIBLE,
            RecoveryCaseStatus.RECOMMENDED,
            RecoveryCaseStatus.POLICY_REVIEW,
            RecoveryCaseStatus.FAILED,
        }
    ),
}

# Actions that contact the customer and are therefore subject to the
# customer-contact cap.
_CONTACT_ACTIONS = frozenset({RecoveryAction.SEND_PAYMENT_LINK, RecoveryAction.SEND_REMINDER})

# Actions that actively pursue recovery, as opposed to safe fallbacks
# (ESCALATE, STOP). Only these are gated by the recovery window and by
# minimum confidence — you must always be able to stop or escalate a case,
# including (especially) one that has gone stale.
_ACTIVE_RECOVERY_ACTIONS = frozenset(
    {RecoveryAction.RETRY_PAYMENT, RecoveryAction.SEND_PAYMENT_LINK, RecoveryAction.SEND_REMINDER}
)
_CONFIDENCE_GATED_ACTIONS = _ACTIVE_RECOVERY_ACTIONS


@dataclass(frozen=True)
class PolicyConfig:
    """Deterministic thresholds. Construct explicitly; do not read env vars here.

    Every field here must be wired through `app.services.policy_service.
    get_policy_config` — a field that exists here but isn't passed there is
    silently un-configurable, which is exactly the kind of gap
    `test_policy_service.py` now guards against.
    """

    policy_version: str = POLICY_VERSION
    max_retry_count: int = 3
    max_recovery_window_days: int = 14
    max_customer_contacts: int = 2
    min_confidence_threshold: float = 0.55
    high_value_amount_threshold: int = 500_000  # smallest currency unit (e.g. paise)
    high_value_min_confidence_threshold: float = 0.75
    # Hard ceiling on what a single recovery may ever pursue. Without this,
    # a corrupted or mis-ingested `Payment.amount` (an extra three zeros)
    # passes on confidence alone and is sent straight to the provider. A
    # bound that has to be raised deliberately is much safer than none.
    max_recovery_amount: int = 10_000_000  # 100,000.00 in major units


@dataclass(frozen=True)
class PolicyEvaluationInput:
    """The facts a policy decision is based on. No PII, no free text."""

    case_status: RecoveryCaseStatus
    proposed_action: RecoveryAction
    attempt_number: int
    days_since_discovery: int
    customer_contact_count: int
    recovery_confidence: float
    amount: int


class PolicyDecision(BaseModel):
    decision: PolicyDecisionType
    reason_codes: list[PolicyReasonCode] = []
    policy_version: str


def evaluate_policy(policy_input: PolicyEvaluationInput, config: PolicyConfig) -> PolicyDecision:
    """Evaluate a proposed recovery action against deterministic safety rules.

    Collects *all* violated rules (not just the first) so callers and audit
    logs get the full picture of why an action was blocked.
    """
    reason_codes: list[PolicyReasonCode] = []

    if policy_input.case_status in TERMINAL_RECOVERY_CASE_STATUSES:
        reason_codes.append(PolicyReasonCode.TERMINAL_STATE_PROTECTED)

    eligible_statuses = _ACTION_ELIGIBLE_STATUSES.get(policy_input.proposed_action, frozenset())
    if policy_input.case_status not in eligible_statuses:
        reason_codes.append(PolicyReasonCode.ACTION_NOT_ELIGIBLE_FOR_STATUS)

    if policy_input.proposed_action == RecoveryAction.RETRY_PAYMENT and (
        policy_input.attempt_number >= config.max_retry_count
    ):
        reason_codes.append(PolicyReasonCode.MAX_RETRIES_REACHED)

    if (
        policy_input.proposed_action in _ACTIVE_RECOVERY_ACTIONS
        and policy_input.days_since_discovery > config.max_recovery_window_days
    ):
        reason_codes.append(PolicyReasonCode.RECOVERY_WINDOW_EXPIRED)

    if policy_input.proposed_action in _CONTACT_ACTIONS and (
        policy_input.customer_contact_count >= config.max_customer_contacts
    ):
        reason_codes.append(PolicyReasonCode.MAX_CONTACTS_REACHED)

    if policy_input.amount <= 0 or policy_input.amount > config.max_recovery_amount:
        reason_codes.append(PolicyReasonCode.AMOUNT_OUT_OF_BOUNDS)

    if policy_input.proposed_action in _CONFIDENCE_GATED_ACTIONS:
        threshold = (
            config.high_value_min_confidence_threshold
            if policy_input.amount >= config.high_value_amount_threshold
            else config.min_confidence_threshold
        )
        if policy_input.recovery_confidence < threshold:
            reason_codes.append(PolicyReasonCode.CONFIDENCE_BELOW_THRESHOLD)

    decision = PolicyDecisionType.BLOCK if reason_codes else PolicyDecisionType.ALLOW
    return PolicyDecision(decision=decision, reason_codes=reason_codes, policy_version=config.policy_version)
