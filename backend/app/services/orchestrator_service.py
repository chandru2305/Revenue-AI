"""The autonomous recovery loop — what makes this a running system rather
than a set of endpoints an operator drives by hand.

One cycle is: discover un-cased failed payments -> diagnose every case
that is ready for it -> optionally execute the ones policy approved. It
adds no new decision-making of its own. Every step calls the same
services the API calls, so the state machine, the deterministic policy
engine, optimistic locking, and the audit trail all apply unchanged.

Three bounds keep an autonomous loop safe to leave running:

1. **Execution is opt-in and off by default** (`ORCHESTRATOR_AUTO_EXECUTE`).
   Diagnosis is read-only reasoning plus a policy decision; execution
   moves money. Automating the first without the second is the
   defensible default, and the one this ships with.
2. **Per-cycle budgets** (`ORCHESTRATOR_MAX_*`) cap how many cases a
   single pass may diagnose or execute, so a large backlog drains
   gradually instead of firing hundreds of provider calls at once.
3. **Failures are isolated.** One case raising never aborts the cycle —
   it is recorded against that case and the loop continues. A cycle that
   half-succeeds is still a truthful, fully-reported cycle.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.service import AIRecommendationService
from app.core.errors import RecoverAIError
from app.core.logging import get_logger, log_event
from app.domain.enums import ActorType, RecoveryCaseStatus
from app.domain.providers.base import PaymentProvider
from app.repositories.recovery_case_repository import RecoveryCaseRepository
from app.schemas.orchestrator import CycleCaseOutcome, RecoveryCycleReport
from app.services import audit_service, diagnosis_service, execution_service, ingestion_service

logger = get_logger(__name__)

# Statuses a cycle will pick up for diagnosis. Matches
# diagnosis_service._DIAGNOSABLE_STATUSES — anything else is either
# already in flight, terminal, or waiting on a webhook.
_READY_FOR_DIAGNOSIS = (
    RecoveryCaseStatus.DISCOVERED,
    RecoveryCaseStatus.ELIGIBLE,
    RecoveryCaseStatus.FAILED,
)


async def run_recovery_cycle(
    session: AsyncSession,
    *,
    ai_service: AIRecommendationService,
    provider: PaymentProvider,
    correlation_id: str,
    auto_execute: bool,
    max_discover: int,
    max_diagnose: int,
    max_execute: int,
) -> RecoveryCycleReport:
    """Run one full pass. Never raises for a per-case failure."""
    cycle_id = uuid.uuid4()
    started_at = datetime.now(UTC)
    started_perf = time.perf_counter()

    log_event(
        logger,
        logging.INFO,
        "recovery_cycle_started",
        cycle_id=str(cycle_id),
        auto_execute=auto_execute,
        max_discover=max_discover,
        max_diagnose=max_diagnose,
        max_execute=max_execute,
    )

    # --- 1. discover ---
    discovery = await ingestion_service.discover_failed_payments(
        session, correlation_id=correlation_id, limit=max_discover
    )

    # --- 2. diagnose everything ready ---
    candidates = await _cases_ready_for_diagnosis(session, limit=max_diagnose)

    outcomes: list[CycleCaseOutcome] = []
    execute_budget = max_execute if auto_execute else 0

    for case_id in candidates:
        outcome = await _process_case(
            session,
            case_id,
            ai_service=ai_service,
            provider=provider,
            correlation_id=correlation_id,
            auto_execute=auto_execute,
            execute_budget_remaining=execute_budget,
        )
        if outcome.executed:
            execute_budget -= 1
        outcomes.append(outcome)

    # --- 3. drain the approved backlog ---
    #
    # Cases left APPROVED by an *earlier* cycle (or by an operator's manual
    # diagnose) would otherwise never be acted on: APPROVED is not a
    # diagnosable status, so step 2 never sees them again. Without this the
    # loop can only ever execute what it diagnosed in the same pass, which
    # makes turning auto-execute on retroactively do nothing — exactly the
    # failure a live run surfaced.
    if auto_execute and execute_budget > 0:
        already_handled = {o.recovery_case_id for o in outcomes}
        backlog = await _approved_backlog(
            session, limit=execute_budget, exclude=already_handled
        )
        for case_id in backlog:
            if execute_budget <= 0:
                break
            outcome = await _execute_approved_case(
                session, case_id, provider=provider, correlation_id=correlation_id
            )
            if outcome.executed:
                execute_budget -= 1
            outcomes.append(outcome)

    finished_at = datetime.now(UTC)
    report = RecoveryCycleReport(
        cycle_id=cycle_id,
        correlation_id=correlation_id,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=round(time.perf_counter() - started_perf, 2),
        auto_execute=auto_execute,
        cases_discovered=discovery.created,
        cases_diagnosed=sum(1 for o in outcomes if o.diagnosed),
        cases_executed=sum(1 for o in outcomes if o.executed),
        cases_failed=sum(1 for o in outcomes if o.error is not None),
        approved=sum(1 for o in outcomes if o.final_status == RecoveryCaseStatus.APPROVED),
        stopped=sum(1 for o in outcomes if o.final_status == RecoveryCaseStatus.STOPPED),
        escalated=sum(1 for o in outcomes if o.final_status == RecoveryCaseStatus.ESCALATED),
        recovered=sum(1 for o in outcomes if o.final_status == RecoveryCaseStatus.RECOVERED),
        outcomes=outcomes,
    )

    # The cycle itself is an actor in the audit trail, not an invisible
    # background process: an operator reading the trail must be able to
    # see that a machine, not a person, moved these cases.
    await audit_service.record_event(
        session,
        entity_type="recovery_cycle",
        entity_id=cycle_id,
        event_type="recovery_cycle_completed",
        actor_type=ActorType.SYSTEM,
        payload={
            "auto_execute": auto_execute,
            "cases_discovered": report.cases_discovered,
            "cases_diagnosed": report.cases_diagnosed,
            "cases_executed": report.cases_executed,
            "cases_failed": report.cases_failed,
            "approved": report.approved,
            "stopped": report.stopped,
            "escalated": report.escalated,
            "duration_seconds": report.duration_seconds,
        },
        correlation_id=correlation_id,
    )
    await session.commit()

    log_event(
        logger,
        logging.INFO,
        "recovery_cycle_completed",
        cycle_id=str(cycle_id),
        cases_discovered=report.cases_discovered,
        cases_diagnosed=report.cases_diagnosed,
        cases_executed=report.cases_executed,
        cases_failed=report.cases_failed,
        duration_seconds=report.duration_seconds,
    )
    return report


async def _cases_ready_for_diagnosis(session: AsyncSession, *, limit: int) -> list[uuid.UUID]:
    """Case ids in a diagnosable status, oldest first — the longest-leaking
    revenue is picked up before newer failures."""
    repo = RecoveryCaseRepository(session)
    collected: list[uuid.UUID] = []
    for status in _READY_FOR_DIAGNOSIS:
        if len(collected) >= limit:
            break
        rows, _total = await repo.list_paginated(
            offset=0, limit=limit - len(collected), status=status
        )
        collected.extend(row.id for row in rows)
    return collected[:limit]


async def _approved_backlog(
    session: AsyncSession, *, limit: int, exclude: set[uuid.UUID]
) -> list[uuid.UUID]:
    """APPROVED cases this cycle hasn't already handled — the backlog left
    by earlier cycles or by an operator diagnosing without executing."""
    repo = RecoveryCaseRepository(session)
    rows, _total = await repo.list_paginated(
        offset=0, limit=limit + len(exclude), status=RecoveryCaseStatus.APPROVED
    )
    return [row.id for row in rows if row.id not in exclude][:limit]


async def _execute_approved_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    provider: PaymentProvider,
    correlation_id: str,
) -> CycleCaseOutcome:
    """Execute one already-APPROVED case. `diagnosed=False` because this
    cycle did not diagnose it — an earlier one did."""
    case_correlation = f"{correlation_id}:{case_id}"
    try:
        execution = await execution_service.execute_recovery_case(
            session, case_id, provider=provider, correlation_id=case_correlation
        )
    except RecoverAIError as exc:
        return CycleCaseOutcome(
            recovery_case_id=case_id,
            final_status=await _current_status(session, case_id),
            diagnosed=False,
            executed=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    return CycleCaseOutcome(
        recovery_case_id=case_id,
        final_status=execution.case_status,
        diagnosed=False,
        executed=execution.executed,
        withheld_reason=None if execution.executed else execution.reason,
    )


async def _process_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    ai_service: AIRecommendationService,
    provider: PaymentProvider,
    correlation_id: str,
    auto_execute: bool,
    execute_budget_remaining: int,
) -> CycleCaseOutcome:
    case_correlation = f"{correlation_id}:{case_id}"

    try:
        diagnosis = await diagnosis_service.diagnose_recovery_case(
            session, case_id, ai_service=ai_service, correlation_id=case_correlation
        )
    except RecoverAIError as exc:
        # Expected, per-case domain failures (a concurrent modification, an
        # illegal transition because something else moved the case first).
        # Isolate and continue — one bad case must not stop the cycle.
        return CycleCaseOutcome(
            recovery_case_id=case_id,
            final_status=await _current_status(session, case_id),
            diagnosed=False,
            executed=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    status = diagnosis.case_status
    if status != RecoveryCaseStatus.APPROVED:
        return CycleCaseOutcome(
            recovery_case_id=case_id, final_status=status, diagnosed=True, executed=False
        )

    # Approved. Whether we act on it is a separate, deliberately gated
    # decision — and when we don't, we say why rather than silently
    # leaving the case sitting there.
    if not auto_execute:
        return CycleCaseOutcome(
            recovery_case_id=case_id,
            final_status=status,
            diagnosed=True,
            executed=False,
            withheld_reason="auto_execute is disabled; approved case left for human review",
        )
    if execute_budget_remaining <= 0:
        return CycleCaseOutcome(
            recovery_case_id=case_id,
            final_status=status,
            diagnosed=True,
            executed=False,
            withheld_reason="per-cycle execution budget exhausted; will be picked up next cycle",
        )

    try:
        execution = await execution_service.execute_recovery_case(
            session, case_id, provider=provider, correlation_id=case_correlation
        )
    except RecoverAIError as exc:
        return CycleCaseOutcome(
            recovery_case_id=case_id,
            final_status=await _current_status(session, case_id),
            diagnosed=True,
            executed=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    return CycleCaseOutcome(
        recovery_case_id=case_id,
        final_status=execution.case_status,
        diagnosed=True,
        executed=execution.executed,
        withheld_reason=None if execution.executed else execution.reason,
    )


async def _current_status(session: AsyncSession, case_id: uuid.UUID) -> RecoveryCaseStatus:
    """Re-read a case's status after a failure, so the report says where the
    case actually ended up rather than where we hoped it would."""
    case = await RecoveryCaseRepository(session).get(case_id)
    if case is None:  # pragma: no cover - the case existed a moment ago
        return RecoveryCaseStatus.DISCOVERED
    return RecoveryCaseStatus(case.status)
