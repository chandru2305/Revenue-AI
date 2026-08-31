"""Decision-quality metrics: how often the strategy's action matches ground truth."""
from __future__ import annotations

from dataclasses import dataclass

from evaluation.baseline.rule_based import BaselineDecision
from evaluation.schemas.dataset_schema import RecoveryAction, SyntheticCase

_ACTIVE_INTERVENTIONS = {
    RecoveryAction.RETRY_PAYMENT,
    RecoveryAction.SEND_PAYMENT_LINK,
    RecoveryAction.SEND_REMINDER,
}
_SAFE_FALLBACKS = {RecoveryAction.STOP, RecoveryAction.ESCALATE}


@dataclass(frozen=True)
class DecisionMetrics:
    intervention_accuracy: float
    appropriate_escalation_rate: float
    inappropriate_intervention_rate: float


def compute_decision_metrics(
    cases: list[SyntheticCase], decisions: list[BaselineDecision]
) -> DecisionMetrics:
    pairs = list(zip(cases, decisions, strict=True))
    total = len(pairs)
    if total == 0:
        return DecisionMetrics(0.0, 0.0, 0.0)

    correct = sum(1 for case, decision in pairs if decision.action == case.ground_truth.expected_action)
    intervention_accuracy = correct / total

    should_escalate = [case for case, _ in pairs if case.ground_truth.expected_action == RecoveryAction.ESCALATE]
    escalated_correctly = sum(
        1
        for case, decision in pairs
        if case.ground_truth.expected_action == RecoveryAction.ESCALATE
        and decision.action == RecoveryAction.ESCALATE
    )
    appropriate_escalation_rate = escalated_correctly / len(should_escalate) if should_escalate else 0.0

    should_not_act = [case for case, _ in pairs if case.ground_truth.expected_action in _SAFE_FALLBACKS]
    wrongly_acted = sum(
        1
        for case, decision in pairs
        if case.ground_truth.expected_action in _SAFE_FALLBACKS and decision.action in _ACTIVE_INTERVENTIONS
    )
    inappropriate_intervention_rate = wrongly_acted / len(should_not_act) if should_not_act else 0.0

    return DecisionMetrics(
        intervention_accuracy=round(intervention_accuracy, 4),
        appropriate_escalation_rate=round(appropriate_escalation_rate, 4),
        inappropriate_intervention_rate=round(inappropriate_intervention_rate, 4),
    )
