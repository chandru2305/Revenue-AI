"""Deterministic scenario generators for synthetic recovery cases.

Each function takes a seeded `random.Random` instance and an integer index
(used only to build a readable case_id) and returns one `SyntheticCase`.
Field relationships are constrained to stay realistic — e.g. a case with
several previous attempts also has more elapsed days and cannot have
`attempt_number = 0`.

Ground truth here is authored independently of `evaluation/baseline`: it
encodes what a careful human reviewer would expect, not what any particular
strategy computes. See docs/evaluation-methodology.md.
"""
from __future__ import annotations

import random
from collections.abc import Callable

from evaluation.schemas.dataset_schema import (
    FailureReason,
    GroundTruth,
    PaymentMethodType,
    RecoveryAction,
    ScenarioType,
    SyntheticCase,
    SyntheticCaseInput,
)

# Mirrors the backend's default PolicyConfig.high_value_amount_threshold (v1).
# Duplicated intentionally — see module docstring on decoupling.
HIGH_VALUE_AMOUNT_THRESHOLD = 500_000  # smallest currency unit (paise)

_MODERATE_AMOUNT_RANGE = (5_000, 150_000)
_HIGH_AMOUNT_RANGE = (500_000, 3_000_000)

_TEMPORARY_FAILURE_REASONS = [
    FailureReason.NETWORK_ERROR,
    FailureReason.GATEWAY_TIMEOUT,
    FailureReason.PROVIDER_ERROR,
]
_CUSTOMER_SIDE_FAILURE_REASONS = [
    FailureReason.INSUFFICIENT_FUNDS,
    FailureReason.EXPIRED_INSTRUMENT,
    FailureReason.AUTHENTICATION_FAILED,
]


def _case_id(scenario: ScenarioType, index: int) -> str:
    return f"{scenario.value}_{index:04d}"


def _amount(rng: random.Random, high_value: bool) -> int:
    low, high = _HIGH_AMOUNT_RANGE if high_value else _MODERATE_AMOUNT_RANGE
    return rng.randint(low, high)


def _method(rng: random.Random) -> PaymentMethodType:
    return rng.choice(list(PaymentMethodType))


def generate_temporary_failure(rng: random.Random, index: int) -> SyntheticCase:
    attempt_number = rng.randint(1, 2)
    days_elapsed = rng.randint(0, 2)
    amount = _amount(rng, high_value=False)
    case_input = SyntheticCaseInput(
        case_id=_case_id(ScenarioType.TEMPORARY_FAILURE, index),
        scenario_type=ScenarioType.TEMPORARY_FAILURE,
        amount=amount,
        payment_method_type=_method(rng),
        failure_reason=rng.choice(_TEMPORARY_FAILURE_REASONS),
        attempt_number=attempt_number,
        days_since_first_attempt=days_elapsed,
        previous_contact_count=0,
        customer_payment_history_success_rate=round(rng.uniform(0.6, 0.95), 2),
        is_high_value=amount >= HIGH_VALUE_AMOUNT_THRESHOLD,
    )
    ground_truth = GroundTruth(
        expected_action=RecoveryAction.RETRY_PAYMENT,
        recoverable=True,
        rationale="Transient gateway/network failure with few prior attempts is typically recoverable via retry.",
    )
    return SyntheticCase(input=case_input, ground_truth=ground_truth)


def generate_repeated_failure(rng: random.Random, index: int) -> SyntheticCase:
    attempt_number = rng.randint(3, 6)
    days_elapsed = rng.randint(7, 20)
    amount = _amount(rng, high_value=False)
    success_rate = round(rng.uniform(0.05, 0.35), 2)
    case_input = SyntheticCaseInput(
        case_id=_case_id(ScenarioType.REPEATED_FAILURE, index),
        scenario_type=ScenarioType.REPEATED_FAILURE,
        amount=amount,
        payment_method_type=_method(rng),
        failure_reason=rng.choice(_TEMPORARY_FAILURE_REASONS + _CUSTOMER_SIDE_FAILURE_REASONS),
        attempt_number=attempt_number,
        days_since_first_attempt=days_elapsed,
        previous_contact_count=min(attempt_number, rng.randint(1, 2)),
        customer_payment_history_success_rate=success_rate,
        is_high_value=amount >= HIGH_VALUE_AMOUNT_THRESHOLD,
    )
    # A very low historical success rate plus many attempts favors a hard
    # stop; a borderline rate favors handing off to a human instead of
    # silently giving up.
    if success_rate < 0.15:
        expected_action = RecoveryAction.STOP
        rationale = "Retry limit effectively exhausted with very low recovery likelihood; stop further automation."
    else:
        expected_action = RecoveryAction.ESCALATE
        rationale = (
            "Multiple failures but non-trivial recovery signal; escalate for human judgment rather than retry."
        )
    ground_truth = GroundTruth(expected_action=expected_action, recoverable=False, rationale=rationale)
    return SyntheticCase(input=case_input, ground_truth=ground_truth)


def generate_customer_side_failure(rng: random.Random, index: int) -> SyntheticCase:
    attempt_number = rng.randint(1, 2)
    amount = _amount(rng, high_value=False)
    case_input = SyntheticCaseInput(
        case_id=_case_id(ScenarioType.CUSTOMER_SIDE_FAILURE, index),
        scenario_type=ScenarioType.CUSTOMER_SIDE_FAILURE,
        amount=amount,
        payment_method_type=_method(rng),
        failure_reason=rng.choice(_CUSTOMER_SIDE_FAILURE_REASONS),
        attempt_number=attempt_number,
        days_since_first_attempt=rng.randint(0, 3),
        previous_contact_count=rng.randint(0, 1),
        customer_payment_history_success_rate=round(rng.uniform(0.5, 0.9), 2),
        is_high_value=amount >= HIGH_VALUE_AMOUNT_THRESHOLD,
    )
    ground_truth = GroundTruth(
        expected_action=RecoveryAction.SEND_PAYMENT_LINK,
        recoverable=True,
        rationale="Instrument-side failure (funds/expiry/auth) won't be fixed by an identical retry; "
        "offer a payment link so the customer can use a different instrument.",
    )
    return SyntheticCase(input=case_input, ground_truth=ground_truth)


def generate_high_value(rng: random.Random, index: int) -> SyntheticCase:
    attempt_number = rng.randint(1, 2)
    amount = _amount(rng, high_value=True)
    failure_reason = rng.choice(_TEMPORARY_FAILURE_REASONS + _CUSTOMER_SIDE_FAILURE_REASONS)
    case_input = SyntheticCaseInput(
        case_id=_case_id(ScenarioType.HIGH_VALUE, index),
        scenario_type=ScenarioType.HIGH_VALUE,
        amount=amount,
        payment_method_type=_method(rng),
        failure_reason=failure_reason,
        attempt_number=attempt_number,
        days_since_first_attempt=rng.randint(0, 3),
        previous_contact_count=0,
        customer_payment_history_success_rate=round(rng.uniform(0.55, 0.95), 2),
        is_high_value=True,
    )
    if failure_reason in _TEMPORARY_FAILURE_REASONS:
        expected_action = RecoveryAction.RETRY_PAYMENT
    else:
        expected_action = RecoveryAction.SEND_PAYMENT_LINK
    ground_truth = GroundTruth(
        expected_action=expected_action,
        recoverable=True,
        rationale="High-value payment: the underlying cause still points to a normal recovery action, "
        "but policy must apply a stricter confidence bar before acting.",
    )
    return SyntheticCase(input=case_input, ground_truth=ground_truth)


def generate_previously_contacted(rng: random.Random, index: int) -> SyntheticCase:
    attempt_number = rng.randint(2, 4)
    amount = _amount(rng, high_value=False)
    case_input = SyntheticCaseInput(
        case_id=_case_id(ScenarioType.PREVIOUSLY_CONTACTED, index),
        scenario_type=ScenarioType.PREVIOUSLY_CONTACTED,
        amount=amount,
        payment_method_type=_method(rng),
        failure_reason=rng.choice(_CUSTOMER_SIDE_FAILURE_REASONS + _TEMPORARY_FAILURE_REASONS),
        attempt_number=attempt_number,
        days_since_first_attempt=rng.randint(5, 14),
        previous_contact_count=rng.randint(2, 3),
        customer_payment_history_success_rate=round(rng.uniform(0.3, 0.7), 2),
        is_high_value=amount >= HIGH_VALUE_AMOUNT_THRESHOLD,
    )
    ground_truth = GroundTruth(
        expected_action=RecoveryAction.STOP,
        recoverable=False,
        rationale="Customer contact cap already reached; further reminders/links would violate contact limits.",
    )
    return SyntheticCase(input=case_input, ground_truth=ground_truth)


def generate_ambiguous(rng: random.Random, index: int) -> SyntheticCase:
    attempt_number = rng.randint(1, 3)
    amount = _amount(rng, high_value=rng.random() < 0.15)
    case_input = SyntheticCaseInput(
        case_id=_case_id(ScenarioType.AMBIGUOUS, index),
        scenario_type=ScenarioType.AMBIGUOUS,
        amount=amount,
        payment_method_type=_method(rng),
        failure_reason=FailureReason.UNKNOWN,
        attempt_number=attempt_number,
        days_since_first_attempt=rng.randint(1, 10),
        previous_contact_count=rng.randint(0, 1),
        customer_payment_history_success_rate=round(rng.uniform(0.4, 0.6), 2),
        is_high_value=amount >= HIGH_VALUE_AMOUNT_THRESHOLD,
    )
    ground_truth = GroundTruth(
        expected_action=RecoveryAction.ESCALATE,
        recoverable=False,
        rationale="Failure cause is unknown and signals are inconclusive; escalate rather than automate blindly.",
    )
    return SyntheticCase(input=case_input, ground_truth=ground_truth)


SCENARIO_GENERATORS: dict[ScenarioType, Callable[[random.Random, int], SyntheticCase]] = {
    ScenarioType.TEMPORARY_FAILURE: generate_temporary_failure,
    ScenarioType.REPEATED_FAILURE: generate_repeated_failure,
    ScenarioType.CUSTOMER_SIDE_FAILURE: generate_customer_side_failure,
    ScenarioType.HIGH_VALUE: generate_high_value,
    ScenarioType.PREVIOUSLY_CONTACTED: generate_previously_contacted,
    ScenarioType.AMBIGUOUS: generate_ambiguous,
}
