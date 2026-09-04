"""Orchestrates the AI diagnosis + recovery-recommendation workflow.

Walks a RecoveryCase through DISCOVERED/ELIGIBLE/FAILED -> DIAGNOSING ->
RECOMMENDED -> POLICY_REVIEW -> APPROVED/STOPPED/ESCALATED, using the
existing state machine (app.domain.state_machine) and policy engine
(app.domain.policy) exactly as Phase 1 defined them. This module adds no
new transitions and no new safety rules — see docs/ai-safety.md for why
that boundary matters.

Idempotency: re-diagnosing a case that isn't currently in DISCOVERED,
ELIGIBLE, or FAILED status is rejected by the state machine itself
(InvalidStateTransitionError -> HTTP 409) — no separate idempotency
mechanism was needed. This does not protect against two concurrent
requests racing on the *same* case at the *same* instant (both reading the
same pre-transition status before either commits); see
docs/ai-architecture.md "Known limitations" for why that's an accepted,
documented gap rather than a Phase 2 fix.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context import build_context
from app.ai.service import AIRecommendationService
from app.core.errors import NotFoundError
from app.db.base import ensure_utc
from app.domain.enums import (
    ActorType,
    DecisionSource,
    PaymentStatus,
    PolicyDecisionType,
    PolicyReasonCode,
    RecoveryAction,
    RecoveryCaseStatus,
)
from app.domain.policy import PolicyConfig, PolicyEvaluationInput, evaluate_policy
from app.domain.providers.base import EXECUTABLE_ACTIONS
from app.domain.state_machine import validate_transition
from app.models.recovery_case import RecoveryCase
from app.repositories.recovery_case_repository import RecoveryCaseRepository
from app.schemas.diagnosis import DiagnosisResponse
from app.services import audit_service
from app.services.concurrency import guard_against_concurrent_modification
from app.services.policy_service import get_policy_config

_DIAGNOSABLE_STATUSES = frozenset(
    {RecoveryCaseStatus.DISCOVERED, RecoveryCaseStatus.ELIGIBLE, RecoveryCaseStatus.FAILED}
)

# BLOCK reasons that mean "this will never work" -> STOPPED. Everything
# else (low confidence, unexpected status) means "unclear" -> ESCALATED.
# This mapping interprets the policy engine's output; it does not change
# or bypass any policy rule.
_HARD_STOP_REASON_CODES = frozenset(
    {
        PolicyReasonCode.MAX_RETRIES_REACHED,
        PolicyReasonCode.RECOVERY_WINDOW_EXPIRED,
        PolicyReasonCode.MAX_CONTACTS_REACHED,
        PolicyReasonCode.AMOUNT_OUT_OF_BOUNDS,
        PolicyReasonCode.TERMINAL_STATE_PROTECTED,
    }
)


def _status_value(status: RecoveryCaseStatus | str) -> str:
    """`case.status` read fresh from the DB comes back as a plain `str`
    (the column type is `String`, not a SQLAlchemy `Enum`), not a
    `RecoveryCaseStatus` instance — only after *this process* assigns an
    enum member does `.value` work directly. Re-coercing here makes every
    call site safe regardless of where the value came from."""
    return RecoveryCaseStatus(status).value


async def diagnose_recovery_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    ai_service: AIRecommendationService,
    correlation_id: str,
    policy_config: PolicyConfig | None = None,
) -> DiagnosisResponse:
    """Public entrypoint — guards the whole operation against a concurrent
    modification of the same case (see app.services.concurrency)."""
    return await guard_against_concurrent_modification(
        session,
        lambda: _diagnose_recovery_case(
            session,
            case_id,
            ai_service=ai_service,
            correlation_id=correlation_id,
            policy_config=policy_config,
        ),
    )


async def _diagnose_recovery_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    ai_service: AIRecommendationService,
    correlation_id: str,
    policy_config: PolicyConfig | None = None,
) -> DiagnosisResponse:
    repo = RecoveryCaseRepository(session)
    case = await repo.get_for_diagnosis(case_id)
    if case is None:
        raise NotFoundError(f"Recovery case '{case_id}' was not found.")

    if case.status not in _DIAGNOSABLE_STATUSES:
        # Not a duplicate check: this is the *only* eligibility gate for
        # (re-)diagnosis, and it's the same table state_machine.py already
        # defines — see module docstring on idempotency.
        validate_transition(RecoveryCaseStatus(case.status), RecoveryCaseStatus.DIAGNOSING)

    await audit_service.record_event(
        session,
        entity_type="recovery_case",
        entity_id=case.id,
        event_type="diagnosis_requested",
        actor_type=ActorType.SYSTEM,
        payload={"case_status": _status_value(case.status)},
        correlation_id=correlation_id,
    )

    if case.status == RecoveryCaseStatus.DISCOVERED:
        await _evaluate_eligibility(session, case, correlation_id=correlation_id)
        if case.status == RecoveryCaseStatus.INELIGIBLE:
            await session.commit()
            return DiagnosisResponse(
                recovery_case_id=case.id,
                case_status=RecoveryCaseStatus(case.status),
                correlation_id=correlation_id,
            )

    await _transition(session, case, RecoveryCaseStatus.DIAGNOSING, correlation_id=correlation_id)

    context = build_context(
        payment=case.payment, customer=case.payment.customer, previous_attempts=list(case.attempts)
    )
    outcome = await ai_service.get_recommendation(context)

    await audit_service.record_event(
        session,
        entity_type="recovery_case",
        entity_id=case.id,
        event_type="ai_diagnosis_created",
        actor_type=ActorType.AI if outcome.decision_source == DecisionSource.AI else ActorType.SYSTEM,
        payload={
            "decision_source": outcome.decision_source.value,
            "diagnosis_category": outcome.recommendation.diagnosis_category.value,
            "model": outcome.model,
            "prompt_version": outcome.prompt_version,
            "latency_ms": round(outcome.latency_ms, 1),
            "retry_count": outcome.retry_count,
            "failure_code": outcome.failure_code,
            "failure_message": outcome.failure_message,
        },
        correlation_id=correlation_id,
    )
    await audit_service.record_event(
        session,
        entity_type="recovery_case",
        entity_id=case.id,
        event_type="recovery_recommendation_created",
        actor_type=ActorType.AI if outcome.decision_source == DecisionSource.AI else ActorType.SYSTEM,
        payload={
            "recommended_action": outcome.recommendation.recommended_action.value,
            "recovery_confidence": outcome.recommendation.recovery_confidence,
            "decision_explanation": outcome.recommendation.decision_explanation,
        },
        correlation_id=correlation_id,
    )

    case.diagnosis_category = outcome.recommendation.diagnosis_category
    case.diagnosis_notes = outcome.recommendation.decision_explanation
    case.recovery_confidence = outcome.recommendation.recovery_confidence
    case.recommended_action = outcome.recommendation.recommended_action

    await _transition(session, case, RecoveryCaseStatus.RECOMMENDED, correlation_id=correlation_id)
    await _transition(session, case, RecoveryCaseStatus.POLICY_REVIEW, correlation_id=correlation_id)

    config = policy_config or get_policy_config()
    days_since_discovery = (datetime.now(UTC) - ensure_utc(case.created_at)).days
    policy_input = PolicyEvaluationInput(
        case_status=RecoveryCaseStatus(case.status),
        proposed_action=outcome.recommendation.recommended_action,
        attempt_number=case.current_attempt_number,
        days_since_discovery=days_since_discovery,
        customer_contact_count=case.customer_contact_count,
        recovery_confidence=outcome.recommendation.recovery_confidence,
        amount=case.payment.amount,
    )
    decision = evaluate_policy(policy_input, config)
    case.policy_version = decision.policy_version

    await audit_service.record_event(
        session,
        entity_type="recovery_case",
        entity_id=case.id,
        event_type="policy_evaluated",
        actor_type=ActorType.POLICY_ENGINE,
        payload={
            "decision": decision.decision.value,
            "reason_codes": [code.value for code in decision.reason_codes],
            "policy_version": decision.policy_version,
            "proposed_action": outcome.recommendation.recommended_action.value,
        },
        correlation_id=correlation_id,
    )

    next_status, transition_reason = _resolve_next_status(
        outcome.recommendation.recommended_action, decision.decision, decision.reason_codes
    )
    await _transition(
        session, case, next_status, correlation_id=correlation_id, reason=transition_reason
    )

    await session.commit()

    return DiagnosisResponse(
        recovery_case_id=case.id,
        case_status=RecoveryCaseStatus(case.status),
        correlation_id=correlation_id,
        decision_source=outcome.decision_source,
        diagnosis_category=outcome.recommendation.diagnosis_category,
        recovery_confidence=outcome.recommendation.recovery_confidence,
        recommended_action=outcome.recommendation.recommended_action,
        decision_explanation=outcome.recommendation.decision_explanation,
        policy_decision=decision.decision,
        policy_reason_codes=decision.reason_codes,
        policy_version=decision.policy_version,
        ai_model=outcome.model,
        ai_prompt_version=outcome.prompt_version,
        ai_latency_ms=round(outcome.latency_ms, 1),
    )


async def _transition(
    session: AsyncSession,
    case: RecoveryCase,
    target: RecoveryCaseStatus,
    *,
    correlation_id: str,
    reason: str | None = None,
) -> None:
    from_status = RecoveryCaseStatus(case.status)
    validate_transition(from_status, target)
    case.status = target
    await session.flush()
    await audit_service.record_event(
        session,
        entity_type="recovery_case",
        entity_id=case.id,
        event_type="recovery_case_status_changed",
        actor_type=ActorType.SYSTEM,
        payload={"from_status": from_status.value, "to_status": target.value, "reason": reason},
        correlation_id=correlation_id,
    )


async def _evaluate_eligibility(session: AsyncSession, case: RecoveryCase, *, correlation_id: str) -> None:
    eligible = case.payment.status == PaymentStatus.FAILED and case.payment.amount > 0
    case.eligible = eligible
    target = RecoveryCaseStatus.ELIGIBLE if eligible else RecoveryCaseStatus.INELIGIBLE
    reason = None if eligible else "Payment is not in a failed state, or has a non-positive amount."
    await _transition(session, case, target, correlation_id=correlation_id, reason=reason)


def _resolve_next_status(
    action: RecoveryAction, decision: PolicyDecisionType, reason_codes: list[PolicyReasonCode]
) -> tuple[RecoveryCaseStatus, str | None]:
    """Maps a policy outcome to the case's next status, plus a human-readable
    reason for the transition (None when the status speaks for itself)."""
    if decision == PolicyDecisionType.ALLOW:
        if action == RecoveryAction.STOP:
            return RecoveryCaseStatus.STOPPED, None
        if action == RecoveryAction.ESCALATE:
            return RecoveryCaseStatus.ESCALATED, None
        if action not in EXECUTABLE_ACTIONS:
            # Policy permits it, but nothing can carry it out. APPROVED
            # means "ready to execute", so routing here would have the case
            # advertise an action that could only ever fail at execution
            # time. Hand it to a human now, with the real reason.
            return (
                RecoveryCaseStatus.ESCALATED,
                f"Action '{action.value}' is permitted by policy but has no executor "
                "implementation; routing to human review instead of APPROVED.",
            )
        return RecoveryCaseStatus.APPROVED, None

    if any(code in _HARD_STOP_REASON_CODES for code in reason_codes):
        return RecoveryCaseStatus.STOPPED, None
    return RecoveryCaseStatus.ESCALATED, None
