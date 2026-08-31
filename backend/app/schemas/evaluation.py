"""Response schema for GET /api/v1/evaluation/summary.

This mirrors the JSON report shape written by
`evaluation/run_evaluation.py` (see evaluation/metrics/report.py). The API
never computes metrics itself — it only surfaces the most recent report
file, or an explicit empty state if no evaluation has been run yet.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FinancialMetrics(BaseModel):
    total_revenue_at_risk: int
    eligible_revenue: int
    recovered_revenue: int
    recovery_rate: float


class DecisionMetrics(BaseModel):
    intervention_accuracy: float
    appropriate_escalation_rate: float
    inappropriate_intervention_rate: float


class SafetyMetrics(BaseModel):
    policy_violations: int
    retry_limit_violations: int
    stopping_rule_violations: int
    unauthorized_actions: int


class OperationalMetrics(BaseModel):
    cases_processed: int
    average_processing_time_ms: float
    throughput_per_second: float


class EvaluationSummaryRead(BaseModel):
    status: str  # "ok" | "no_evaluation_run"
    run_id: str | None = None
    generated_at: datetime | None = None
    strategy: str | None = None
    dataset_count: int | None = None
    dataset_seed: int | None = None
    financial: FinancialMetrics | None = None
    decision: DecisionMetrics | None = None
    safety: SafetyMetrics | None = None
    operational: OperationalMetrics | None = None


class RecoverySummaryRead(BaseModel):
    """GET /api/v1/evaluation/recovery-summary — computed live from actual
    `recovery_cases` rows in this database. Deliberately a SEPARATE
    endpoint from `/evaluation/summary` (the synthetic-dataset report):
    mixing "500 simulated cases" with "however many real cases exist in
    this database" into one number would misrepresent both. See
    docs/razorpay-integration.md "Simulated vs. real evaluation."

    `recovery_rate` is confirmed-recovered / eligible — never a "payment
    link created" count. Creating a link is not recovered revenue.
    """

    source: str = "live_database"
    generated_at: datetime

    cases_total: int
    cases_eligible: int
    cases_by_status: dict[str, int]

    total_revenue_at_risk: int
    eligible_revenue: int
    confirmed_recovered_revenue: int
    outstanding_revenue: int
    recovery_rate: float

    recovery_attempts: int
    successful_payment_links_created: int
    average_recovery_amount: float

    escalation_rate: float
    stop_rate: float
    provider_failure_rate: float
