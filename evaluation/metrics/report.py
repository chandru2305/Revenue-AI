"""Assembles a JSON report matching backend/app/schemas/evaluation.py.

Keeping this shape in sync with the backend schema by hand (rather than a
shared import) is a deliberate consequence of keeping `evaluation/`
dependency-free of `backend/`. If the two drift, `GET /api/v1/evaluation/summary`
will fail Pydantic validation loudly rather than silently showing wrong data.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from evaluation.metrics.decision import DecisionMetrics
from evaluation.metrics.financial import FinancialMetrics
from evaluation.metrics.operational import OperationalMetrics
from evaluation.metrics.safety import SafetyMetrics
from evaluation.schemas.dataset_schema import Dataset


def build_report(
    dataset: Dataset,
    strategy_name: str,
    financial: FinancialMetrics,
    decision: DecisionMetrics,
    safety: SafetyMetrics,
    operational: OperationalMetrics,
) -> dict:
    return {
        "run_id": str(uuid.uuid4()),
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": strategy_name,
        "dataset_count": dataset.count,
        "dataset_seed": dataset.seed,
        "financial": {
            "total_revenue_at_risk": financial.total_revenue_at_risk,
            "eligible_revenue": financial.eligible_revenue,
            "recovered_revenue": financial.recovered_revenue,
            "recovery_rate": financial.recovery_rate,
        },
        "decision": {
            "intervention_accuracy": decision.intervention_accuracy,
            "appropriate_escalation_rate": decision.appropriate_escalation_rate,
            "inappropriate_intervention_rate": decision.inappropriate_intervention_rate,
        },
        "safety": {
            "policy_violations": safety.policy_violations,
            "retry_limit_violations": safety.retry_limit_violations,
            "stopping_rule_violations": safety.stopping_rule_violations,
            "unauthorized_actions": safety.unauthorized_actions,
        },
        "operational": {
            "cases_processed": operational.cases_processed,
            "average_processing_time_ms": operational.average_processing_time_ms,
            "throughput_per_second": operational.throughput_per_second,
        },
    }
