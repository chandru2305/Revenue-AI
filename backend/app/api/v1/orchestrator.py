"""POST /api/v1/orchestrator/cycle — run one pass of the autonomous
recovery loop on demand.

The same service the background loop calls. Exposed as an endpoint so a
cycle can be triggered deliberately (a demo, an operator draining a
backlog, a cron hitting the API) without enabling the background runner.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.dependencies import get_ai_service
from app.ai.service import AIRecommendationService
from app.core.config import get_settings
from app.core.logging import get_correlation_id
from app.db.session import get_db
from app.domain.providers.base import PaymentProvider
from app.payments.dependencies import get_payment_provider
from app.repositories.audit_event_repository import AuditEventRepository
from app.schemas.orchestrator import (
    LastCycleSummary,
    OrchestratorStatusResponse,
    RecoveryCycleReport,
)
from app.services import orchestrator_runner, orchestrator_service

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])

# How many recent cycle / diagnosis events to sample for the console.
# Bounded so the status endpoint stays cheap no matter how long the agent
# has been running.
_CYCLE_SAMPLE = 100
_DIAGNOSIS_SAMPLE = 50


@router.get("/status", response_model=OrchestratorStatusResponse)
async def get_status(session: AsyncSession = Depends(get_db)) -> OrchestratorStatusResponse:
    """Read-only snapshot of the autonomous recovery agent: whether the
    background loop is running, and what the last cycle did — reconstructed
    from the append-only audit trail, so the console and the trail can
    never disagree. Starts nothing and changes nothing.
    """
    settings = get_settings()
    runner = orchestrator_runner.status()
    audit_repo = AuditEventRepository(session)

    cycle_rows, cycles_completed = await audit_repo.list_paginated(
        offset=0,
        limit=_CYCLE_SAMPLE,
        entity_type="recovery_cycle",
        event_type="recovery_cycle_completed",
    )

    last_cycle: LastCycleSummary | None = None
    if cycle_rows:
        latest = cycle_rows[0]
        p = latest.payload
        last_cycle = LastCycleSummary(
            completed_at=latest.created_at,
            correlation_id=latest.correlation_id,
            auto_execute=bool(p.get("auto_execute", False)),
            cases_discovered=int(p.get("cases_discovered", 0)),
            cases_diagnosed=int(p.get("cases_diagnosed", 0)),
            cases_executed=int(p.get("cases_executed", 0)),
            cases_failed=int(p.get("cases_failed", 0)),
            approved=int(p.get("approved", 0)),
            stopped=int(p.get("stopped", 0)),
            escalated=int(p.get("escalated", 0)),
            duration_seconds=float(p.get("duration_seconds", 0.0)),
        )

    diag_rows, _ = await audit_repo.list_paginated(
        offset=0,
        limit=_DIAGNOSIS_SAMPLE,
        entity_type="recovery_case",
        event_type="ai_diagnosis_created",
    )
    ai_latencies = [
        float(row.payload["latency_ms"])
        for row in diag_rows
        if row.payload.get("decision_source") == "ai" and row.payload.get("latency_ms") is not None
    ]
    average_ai_latency_ms = round(sum(ai_latencies) / len(ai_latencies), 1) if ai_latencies else None

    if runner["errored"]:
        agent_state = "error"
    elif runner["running"]:
        agent_state = "running"
    else:
        agent_state = "idle"

    return OrchestratorStatusResponse(
        enabled=runner["enabled"],
        running=runner["running"],
        errored=runner["errored"],
        auto_execute=settings.orchestrator_auto_execute,
        interval_seconds=settings.orchestrator_interval_seconds,
        cycles_completed=cycles_completed,
        last_cycle=last_cycle,
        recent_ai_diagnoses=len(ai_latencies),
        average_ai_latency_ms=average_ai_latency_ms,
        agent_state=agent_state,
    )


@router.post("/cycle", response_model=RecoveryCycleReport)
async def run_cycle(
    auto_execute: bool | None = Query(
        default=None,
        description=(
            "Override ORCHESTRATOR_AUTO_EXECUTE for this cycle only. Executing "
            "moves money; policy is still re-checked with fresh data before any "
            "provider call, and per-cycle budgets still apply."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    ai_service: AIRecommendationService = Depends(get_ai_service),
    provider: PaymentProvider = Depends(get_payment_provider),
) -> RecoveryCycleReport:
    """Discover un-cased failed payments, diagnose everything ready, and
    (when auto-execute is on) execute what policy approved.

    Adds no decision-making of its own — every step goes through the same
    state machine, policy engine, and audit trail as the individual
    endpoints.
    """
    settings = get_settings()
    correlation_id = get_correlation_id() or str(uuid.uuid4())
    return await orchestrator_service.run_recovery_cycle(
        session,
        ai_service=ai_service,
        provider=provider,
        correlation_id=correlation_id,
        auto_execute=settings.orchestrator_auto_execute if auto_execute is None else auto_execute,
        max_discover=settings.orchestrator_max_discover,
        max_diagnose=settings.orchestrator_max_diagnose,
        max_execute=settings.orchestrator_max_execute,
    )
