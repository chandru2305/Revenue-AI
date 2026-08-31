"""Financial metrics.

Phase 1 does not execute real recovery actions (no provider integration
yet — see docs). "Recovered revenue" is therefore a *simulated* proxy: a
case counts as recovered only if the strategy's predicted action matches
the ground-truth expected action AND the ground truth marks the case as
recoverable. This is an explicit modeling assumption, not a measured
outcome from a live payment gateway. See docs/evaluation-methodology.md.
"""
from __future__ import annotations

from dataclasses import dataclass

from evaluation.baseline.rule_based import BaselineDecision
from evaluation.schemas.dataset_schema import RecoveryAction, SyntheticCase

_ACTIVE_INTERVENTIONS = {
    RecoveryAction.RETRY_PAYMENT,
    RecoveryAction.SEND_PAYMENT_LINK,
    RecoveryAction.SEND_REMINDER,
}


@dataclass(frozen=True)
class FinancialMetrics:
    total_revenue_at_risk: int
    eligible_revenue: int
    recovered_revenue: int
    recovery_rate: float


def compute_financial_metrics(
    cases: list[SyntheticCase], decisions: list[BaselineDecision]
) -> FinancialMetrics:
    total_revenue_at_risk = sum(case.input.amount for case in cases)
    eligible_revenue = sum(
        case.input.amount
        for case, decision in zip(cases, decisions, strict=True)
        if decision.action in _ACTIVE_INTERVENTIONS
    )
    recovered_revenue = sum(
        case.input.amount
        for case, decision in zip(cases, decisions, strict=True)
        if decision.action == case.ground_truth.expected_action and case.ground_truth.recoverable
    )
    recovery_rate = recovered_revenue / eligible_revenue if eligible_revenue else 0.0

    return FinancialMetrics(
        total_revenue_at_risk=total_revenue_at_risk,
        eligible_revenue=eligible_revenue,
        recovered_revenue=recovered_revenue,
        recovery_rate=round(recovery_rate, 4),
    )
