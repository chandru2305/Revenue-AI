"""Response schemas for the autonomous recovery loop."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import RecoveryCaseStatus


class CycleCaseOutcome(BaseModel):
    """What the cycle did to one case, and where it left it."""

    recovery_case_id: uuid.UUID
    final_status: RecoveryCaseStatus
    diagnosed: bool
    executed: bool
    # Populated when the cycle deliberately did NOT execute an approved
    # case — e.g. auto-execute is off, or the per-cycle execution budget
    # was spent. Never a silent skip.
    withheld_reason: str | None = None
    error: str | None = None


class RecoveryCycleReport(BaseModel):
    """One pass of discover -> diagnose -> (optionally) execute.

    Every number here is counted from real transitions the cycle observed,
    not inferred: a case is only `executed` if `execute_recovery_case`
    returned `executed=True`.
    """

    cycle_id: uuid.UUID
    correlation_id: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float

    auto_execute: bool = Field(
        description="Whether this cycle was permitted to execute approved cases."
    )

    cases_discovered: int = Field(description="New cases opened from un-cased failed payments.")
    cases_diagnosed: int
    cases_executed: int
    cases_failed: int = Field(description="Cases whose diagnose/execute raised, and were skipped.")

    # Terminal/near-terminal distribution after this cycle, counted from
    # the cases this cycle actually touched.
    approved: int = 0
    stopped: int = 0
    escalated: int = 0
    recovered: int = 0

    outcomes: list[CycleCaseOutcome] = []


class LastCycleSummary(BaseModel):
    """The most recent completed cycle, reconstructed from its
    `recovery_cycle_completed` audit event — so the agent console shows the
    same numbers the append-only trail recorded, not a separate tally."""

    completed_at: datetime
    correlation_id: str | None = None
    auto_execute: bool
    cases_discovered: int
    cases_diagnosed: int
    cases_executed: int
    cases_failed: int
    approved: int
    stopped: int
    escalated: int
    duration_seconds: float


class OrchestratorStatusResponse(BaseModel):
    """Read-only view of the autonomous recovery agent for the dashboard.

    Every field is derived from configuration or the audit trail — this
    endpoint starts nothing, changes nothing, and re-checks no policy.
    """

    # Background runner (app/services/orchestrator_runner.py).
    enabled: bool = Field(description="ORCHESTRATOR_ENABLED — is the background loop switched on.")
    running: bool = Field(description="Is the background loop's asyncio task alive right now.")
    errored: bool = Field(description="Did the background loop self-terminate with an exception.")
    auto_execute: bool = Field(description="ORCHESTRATOR_AUTO_EXECUTE — may a cycle move money.")
    interval_seconds: int

    # Derived from the append-only audit trail.
    cycles_completed: int = Field(description="Total `recovery_cycle_completed` events on record.")
    last_cycle: LastCycleSummary | None = None
    recent_ai_diagnoses: int = Field(
        default=0, description="AI-sourced diagnoses in the sampled recent window."
    )
    average_ai_latency_ms: float | None = Field(
        default=None, description="Mean provider latency over that sampled window."
    )

    # A coarse status label for the console header.
    agent_state: str = Field(description='"running" | "idle" | "error"')
