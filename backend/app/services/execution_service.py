"""Orchestrates bounded recovery execution: APPROVED -> EXECUTING -> a
real (Test Mode) Razorpay Payment Link, or an explained ESCALATE.

This module contains NO policy logic of its own — it re-evaluates the
*same* `app.domain.policy.evaluate_policy` function diagnosis_service
uses, with fresh data, and does not alter its rules. See
docs/razorpay-integration.md "Re-check before execution" for why a
second check is necessary even though the case was already APPROVED once.

Amount safety: the executor NEVER accepts an amount from the API request
(there isn't one) — it is always read from the canonical `Payment.amount`
row, and asserted against what's about to be sent to the provider right
before the call. See docs/razorpay-integration.md "Amount safety".

Execution only ever reaches RECOVERED via a provider-confirmed payment
(webhook_service, not this module) — creating a Payment Link is "recovery
initiated," never "recovered." See docs/razorpay-integration.md.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailedError
from app.db.base import ensure_utc
from app.domain.enums import (
    ActorType,
    PolicyDecisionType,
    ProviderFailureCategory,
    RecoveryAction,
    RecoveryAttemptStatus,
    RecoveryCaseStatus,
)
from app.domain.policy import PolicyDecision, PolicyEvaluationInput, evaluate_policy
from app.domain.providers.base import (
    EXECUTABLE_ACTIONS,
    CreatePaymentLinkRequest,
    PaymentLinkSnapshot,
    PaymentProvider,
    PaymentProviderAmbiguousError,
    PaymentProviderAuthError,
    PaymentProviderError,
    PaymentProviderRateLimitError,
    PaymentProviderTimeoutError,
    PaymentProviderValidationError,
)
from app.domain.state_machine import validate_transition
from app.models.recovery_attempt import RecoveryAttempt
from app.models.recovery_case import RecoveryCase
from app.models.recovery_payment_request import RecoveryPaymentRequest
from app.repositories.recovery_case_repository import RecoveryCaseRepository
from app.schemas.execution import ExecutionResponse
from app.services import audit_service
from app.services.concurrency import guard_against_concurrent_modification
from app.services.policy_service import get_policy_config

_PROVIDER_NAME = "razorpay"

_ERROR_TO_FAILURE_CATEGORY: dict[type[PaymentProviderError], ProviderFailureCategory] = {
    PaymentProviderAuthError: ProviderFailureCategory.PROVIDER_AUTH_ERROR,
    PaymentProviderRateLimitError: ProviderFailureCategory.PROVIDER_RATE_LIMIT,
    PaymentProviderValidationError: ProviderFailureCategory.PROVIDER_VALIDATION_ERROR,
    # The real RazorpayPaymentProvider always converts a create-time
    # timeout into PaymentProviderAmbiguousError (see
    # app/payments/providers/razorpay.py) before this map is ever
    # consulted for it — this entry only matters for a fake/future
    # provider that raises a bare timeout directly.
    PaymentProviderTimeoutError: ProviderFailureCategory.PROVIDER_TIMEOUT,
}


def _status_value(status: RecoveryCaseStatus | str) -> str:
    return RecoveryCaseStatus(status).value


async def execute_recovery_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    provider: PaymentProvider,
    correlation_id: str,
) -> ExecutionResponse:
    """Public entrypoint — guards against a concurrent modification of the
    same case, exactly like diagnosis_service.diagnose_recovery_case."""
    return await guard_against_concurrent_modification(
        session,
        lambda: _execute_recovery_case(
            session, case_id, provider=provider, correlation_id=correlation_id
        ),
    )


async def _execute_recovery_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    provider: PaymentProvider,
    correlation_id: str,
) -> ExecutionResponse:
    repo = RecoveryCaseRepository(session)
    case = await repo.get_for_execution(case_id)
    if case is None:
        raise NotFoundError(f"Recovery case '{case_id}' was not found.")

    # The only legal predecessor of EXECUTING is APPROVED — this single
    # check is both the authorization gate ("only an approved case may
    # execute") and the idempotency gate ("a case already executing,
    # recovered, or escalated cannot execute again"), exactly like
    # diagnosis_service's equivalent check.
    validate_transition(RecoveryCaseStatus(case.status), RecoveryCaseStatus.EXECUTING)

    await audit_service.record_event(
        session,
        entity_type="recovery_case",
        entity_id=case.id,
        event_type="execution_requested",
        actor_type=ActorType.SYSTEM,
        payload={"case_status": _status_value(case.status)},
        correlation_id=correlation_id,
    )

    # Re-check deterministic eligibility with fresh data. The AI is not
    # re-run — `case.recommended_action` / `case.recovery_confidence` are
    # whatever diagnosis_service last wrote.
    decision = _recheck_policy(case)
    await audit_service.record_event(
        session,
        entity_type="recovery_case",
        entity_id=case.id,
        event_type="policy_rechecked",
        actor_type=ActorType.POLICY_ENGINE,
        payload={
            "decision": decision.decision.value,
            "reason_codes": [code.value for code in decision.reason_codes],
            "policy_version": decision.policy_version,
        },
        correlation_id=correlation_id,
    )

    await _transition(session, case, RecoveryCaseStatus.EXECUTING, correlation_id=correlation_id)

    if decision.decision == PolicyDecisionType.BLOCK:
        return await _escalate(
            session,
            case,
            reason=f"Policy re-check blocked execution: {', '.join(c.value for c in decision.reason_codes)}",
            correlation_id=correlation_id,
            decision=decision,
        )

    # Defence in depth: diagnosis_service already refuses to route a
    # non-executable action to APPROVED (see `_resolve_next_status`), so a
    # case reaching here with one means it was approved under an older
    # build or edited out of band. Escalate rather than trust it.
    proposed_action = RecoveryAction(case.recommended_action)
    if proposed_action not in EXECUTABLE_ACTIONS:
        return await _escalate(
            session,
            case,
            reason=f"Action '{proposed_action.value}' is not yet implemented by the executor.",
            correlation_id=correlation_id,
            decision=decision,
        )

    await session.commit()  # end the local transaction before the external HTTP call

    return await _create_payment_link(
        session, case, provider=provider, correlation_id=correlation_id, decision=decision
    )


def _recheck_policy(case: RecoveryCase) -> PolicyDecision:
    config = get_policy_config()
    days_since_discovery = (datetime.now(UTC) - ensure_utc(case.created_at)).days
    policy_input = PolicyEvaluationInput(
        case_status=RecoveryCaseStatus(case.status),
        proposed_action=RecoveryAction(case.recommended_action),
        attempt_number=case.current_attempt_number,
        days_since_discovery=days_since_discovery,
        customer_contact_count=case.customer_contact_count,
        recovery_confidence=case.recovery_confidence or 0.0,
        amount=case.payment.amount,
    )
    return evaluate_policy(policy_input, config)


async def _create_payment_link(
    session: AsyncSession,
    case: RecoveryCase,
    *,
    provider: PaymentProvider,
    correlation_id: str,
    decision: PolicyDecision,
) -> ExecutionResponse:
    settings = get_settings()
    execution_reference = str(uuid.uuid4())
    amount = case.payment.amount  # canonical — never from a request
    currency = case.payment.currency

    attempt = RecoveryAttempt(
        recovery_case_id=case.id,
        action=RecoveryAction.SEND_PAYMENT_LINK,
        status=RecoveryAttemptStatus.IN_PROGRESS,
        provider=_PROVIDER_NAME,
        amount=amount,
        currency=currency,
        idempotency_key=execution_reference,
        correlation_id=correlation_id,
        started_at=datetime.now(UTC),
    )
    session.add(attempt)
    await session.flush()

    await audit_service.record_event(
        session,
        entity_type="recovery_attempt",
        entity_id=attempt.id,
        event_type="execution_started",
        actor_type=ActorType.SYSTEM,
        payload={"action": attempt.action.value, "amount": amount, "currency": currency},
        correlation_id=correlation_id,
    )
    await session.commit()

    if amount != case.payment.amount:  # defensive: canonical amount must never drift
        raise ValidationFailedError("Recovery amount does not match the canonical payment amount.")

    expire_by = datetime.now(UTC) + timedelta(hours=settings.recovery_payment_link_expiry_hours)
    request = CreatePaymentLinkRequest(
        reference_id=execution_reference,
        amount=amount,
        currency=currency,
        description=f"RecoverAI payment recovery — case {case.id}",
        expire_by=expire_by,
    )

    try:
        link = await provider.create_payment_link(request)
    except PaymentProviderAmbiguousError as exc:
        return await _handle_ambiguous_creation(
            session, case, attempt, provider=provider, reference_id=execution_reference, error=exc,
            correlation_id=correlation_id,
        )
    except PaymentProviderError as exc:
        return await _handle_definite_failure(
            session, case, attempt, error=exc, correlation_id=correlation_id
        )

    return await _record_payment_link_created(
        session, case, attempt, link, correlation_id=correlation_id
    )


async def _record_payment_link_created(
    session: AsyncSession,
    case: RecoveryCase,
    attempt: RecoveryAttempt,
    link: PaymentLinkSnapshot,
    *,
    correlation_id: str,
) -> ExecutionResponse:
    payment_request = RecoveryPaymentRequest(
        recovery_case_id=case.id,
        recovery_attempt_id=attempt.id,
        provider=_PROVIDER_NAME,
        provider_reference=link.provider_reference,
        reference_id=link.reference_id or attempt.idempotency_key or "",
        short_url=link.short_url,
        amount=link.amount,
        amount_paid=link.amount_paid,
        currency=link.currency,
        status=link.status,
        expires_at=link.expires_at,
    )
    session.add(payment_request)

    attempt.status = RecoveryAttemptStatus.SUCCEEDED
    attempt.provider_reference = link.provider_reference
    attempt.completed_at = datetime.now(UTC)
    case.current_attempt_number += 1
    await session.flush()

    await audit_service.record_event(
        session,
        entity_type="recovery_case",
        entity_id=case.id,
        event_type="payment_link_created",
        actor_type=ActorType.SYSTEM,
        payload={
            "provider_reference": link.provider_reference,
            "short_url": link.short_url,
            "amount": link.amount,
            "currency": link.currency,
            "expires_at": link.expires_at.isoformat() if link.expires_at else None,
        },
        correlation_id=correlation_id,
    )
    await session.commit()

    return ExecutionResponse(
        recovery_case_id=case.id,
        case_status=RecoveryCaseStatus(case.status),
        correlation_id=correlation_id,
        executed=True,
        policy_decision=PolicyDecisionType.ALLOW,
        provider_reference=link.provider_reference,
        short_url=link.short_url,
        payment_link_status=link.status,
        amount=link.amount,
        currency=link.currency,
        expires_at=link.expires_at,
    )


async def _handle_ambiguous_creation(
    session: AsyncSession,
    case: RecoveryCase,
    attempt: RecoveryAttempt,
    *,
    provider: PaymentProvider,
    reference_id: str,
    error: PaymentProviderError,
    correlation_id: str,
) -> ExecutionResponse:
    await audit_service.record_event(
        session,
        entity_type="recovery_attempt",
        entity_id=attempt.id,
        event_type="provider_ambiguous_result",
        actor_type=ActorType.SYSTEM,
        payload={"error": str(error)},
        correlation_id=correlation_id,
    )

    reconciled: PaymentLinkSnapshot | None = None
    reconciliation_error: str | None = None
    try:
        reconciled = await provider.find_payment_link_by_reference(reference_id)
    except PaymentProviderError as exc:
        reconciliation_error = str(exc)

    await audit_service.record_event(
        session,
        entity_type="recovery_attempt",
        entity_id=attempt.id,
        event_type="provider_state_reconciliation",
        actor_type=ActorType.SYSTEM,
        payload={"found": reconciled is not None, "error": reconciliation_error},
        correlation_id=correlation_id,
    )
    await session.commit()

    if reconciled is not None:
        # The link WAS actually created despite the ambiguous response —
        # adopt it rather than creating a duplicate.
        return await _record_payment_link_created(
            session, case, attempt, reconciled, correlation_id=correlation_id
        )

    return await _handle_definite_failure(
        session,
        case,
        attempt,
        error=error,
        correlation_id=correlation_id,
        failure_category=ProviderFailureCategory.AMBIGUOUS_RESULT,
        reason="Provider response was ambiguous and could not be reconciled; manual review required.",
    )


async def _handle_definite_failure(
    session: AsyncSession,
    case: RecoveryCase,
    attempt: RecoveryAttempt,
    *,
    error: PaymentProviderError,
    correlation_id: str,
    failure_category: ProviderFailureCategory | None = None,
    reason: str | None = None,
) -> ExecutionResponse:
    category = failure_category or _ERROR_TO_FAILURE_CATEGORY.get(
        type(error), ProviderFailureCategory.UNKNOWN_PROVIDER_ERROR
    )
    attempt.status = RecoveryAttemptStatus.FAILED
    attempt.failure_code = category.value
    attempt.failure_message = str(error)
    attempt.completed_at = datetime.now(UTC)
    await session.flush()

    escalation_reason = reason or f"Provider call failed ({category.value}): {error}"
    return await _escalate(
        session, case, reason=escalation_reason, correlation_id=correlation_id, decision=None
    )


async def _escalate(
    session: AsyncSession,
    case: RecoveryCase,
    *,
    reason: str,
    correlation_id: str,
    decision: PolicyDecision | None,
) -> ExecutionResponse:
    await _transition(
        session, case, RecoveryCaseStatus.ESCALATED, correlation_id=correlation_id, reason=reason
    )
    await session.commit()

    return ExecutionResponse(
        recovery_case_id=case.id,
        case_status=RecoveryCaseStatus(case.status),
        correlation_id=correlation_id,
        executed=False,
        reason=reason,
        policy_decision=decision.decision if decision is not None else None,
        policy_reason_codes=decision.reason_codes if decision is not None else [],
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
