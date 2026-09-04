"""Processes verified Razorpay webhook events.

Signature verification happens in the API layer (app.api.v1.webhooks)
before this module is ever called — this module assumes the event is
authentic and focuses on: deduplication, locating the affected case, and
making the one state change a given event authorizes. It never trusts a
client-supplied "status": the only path from "payment succeeded" to
`RECOVERED` is through here, driven by a signature-verified event.

`RECOVERED` requires a `payment_link.paid` event carrying the full
requested amount — see `_apply_payment_link_event` and
docs/razorpay-integration.md "Recovered means confirmed paid."
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import (
    ActorType,
    ProviderFailureCategory,
    RecoveryCaseStatus,
    RecoveryPaymentRequestStatus,
)
from app.domain.state_machine import validate_transition
from app.models.processed_webhook_event import ProcessedWebhookEvent
from app.models.recovery_attempt import RecoveryAttempt
from app.models.recovery_case import RecoveryCase
from app.payments.webhooks import ParsedWebhookEvent
from app.repositories.recovery_case_repository import RecoveryCaseRepository
from app.repositories.recovery_payment_request_repository import RecoveryPaymentRequestRepository
from app.services import audit_service
from app.services.concurrency import guard_against_concurrent_modification

_PROVIDER_NAME = "razorpay"

# payment_link.paid only ever moves a case forward if it's still where we
# left it (EXECUTING) — a case that's already RECOVERED, or that moved on
# some other path, is left alone. Terminal-state protection here is the
# same principle as the policy engine's, just expressed as a transition
# check instead of a policy rule.
_RELEVANT_LINK_STATUSES = frozenset(
    {
        RecoveryPaymentRequestStatus.PAID.value,
        RecoveryPaymentRequestStatus.EXPIRED.value,
        RecoveryPaymentRequestStatus.CANCELLED.value,
    }
)


@dataclass(frozen=True)
class WebhookProcessResult:
    status: str  # "processed" | "duplicate" | "ignored"
    event_type: str


def _unmatched_link_entity_id(payment_link_id: str) -> uuid.UUID:
    """Deterministic (not random) so repeated webhooks for the same
    unrecognized link share one traceable audit entity_id instead of each
    getting a fresh, unrelated one."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"razorpay:unmatched-payment-link:{payment_link_id}")


async def process_webhook_event(
    session: AsyncSession, event: ParsedWebhookEvent, *, correlation_id: str
) -> WebhookProcessResult:
    # Claim the event BEFORE touching anything else — see `_claim_event`.
    if not await _claim_event(session, event):
        return WebhookProcessResult(status="duplicate", event_type=event.event_type)

    if event.payment_link_id is None or event.payment_link_status not in _RELEVANT_LINK_STATUSES:
        await session.commit()
        return WebhookProcessResult(status="ignored", event_type=event.event_type)

    result_status = await guard_against_concurrent_modification(
        session, lambda: _apply_payment_link_event(session, event, correlation_id=correlation_id)
    )
    return WebhookProcessResult(status=result_status, event_type=event.event_type)


async def _claim_event(session: AsyncSession, event: ParsedWebhookEvent) -> bool:
    """Insert this event's dedup row. Returns False if it was already
    claimed — by an earlier delivery, or by a concurrent one that won the
    race for the unique constraint on `dedup_key`.

    Ordering matters and is the point of this function. Claiming *first*
    means the `IntegrityError` rollback can only ever discard this one
    INSERT. The previous ordering claimed the event last, so losing the
    race rolled back an already-applied status transition and
    `recovered_amount` increment while still reporting "processed" — the
    revenue total was saved only by the independent optimistic-lock guard,
    and the return value was a lie either way.

    A claim that is made and then abandoned (an exception later in
    processing rolls the whole transaction back, including this row) is
    correct too: the event genuinely wasn't processed, so a Razorpay
    redelivery should — and now will — retry it.
    """
    stmt = select(ProcessedWebhookEvent).where(ProcessedWebhookEvent.dedup_key == event.dedup_key)
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return False

    session.add(
        ProcessedWebhookEvent(provider=_PROVIDER_NAME, event_type=event.event_type, dedup_key=event.dedup_key)
    )
    try:
        await session.flush()
    except IntegrityError:
        # Lost the race to a concurrent delivery of the same event. Nothing
        # else has been mutated yet, so this rollback is contained.
        await session.rollback()
        return False
    return True


async def _apply_payment_link_event(
    session: AsyncSession, event: ParsedWebhookEvent, *, correlation_id: str
) -> str:
    """Returns "processed" if a case was actually affected, "ignored"
    otherwise (unmatched link, or the case had already moved on).

    The event is already claimed in the dedup ledger by the time this runs
    (see `_claim_event`), so nothing here re-records it; the surrounding
    transaction commits that claim alongside whatever this applies."""
    assert event.payment_link_id is not None  # guaranteed by process_webhook_event's guard

    payment_request_repo = RecoveryPaymentRequestRepository(session)
    payment_request = await payment_request_repo.get_by_provider_reference(event.payment_link_id)
    if payment_request is None:
        # A webhook for a payment link we don't recognize — could be from
        # a different environment/account. Record it as ignored, not an
        # error: we only ever act on links we ourselves created.
        await audit_service.record_event(
            session,
            entity_type="webhook",
            entity_id=_unmatched_link_entity_id(event.payment_link_id),
            event_type="webhook_unmatched_payment_link",
            actor_type=ActorType.SYSTEM,
            payload={"payment_link_id": event.payment_link_id, "event_type": event.event_type},
            correlation_id=correlation_id,
        )
        await session.commit()
        return "ignored"

    case_repo = RecoveryCaseRepository(session)
    case = await case_repo.get_for_execution(payment_request.recovery_case_id)
    if case is None:  # pragma: no cover - defensive; FK guarantees this can't happen
        await session.commit()
        return "ignored"

    new_status = RecoveryPaymentRequestStatus(event.payment_link_status)
    payment_request.status = new_status
    payment_request.amount_paid = event.amount_paid or payment_request.amount_paid

    is_paid = new_status == RecoveryPaymentRequestStatus.PAID
    payment_event_type = "payment_confirmed" if is_paid else "payment_not_recovered"
    await audit_service.record_event(
        session,
        entity_type="recovery_case",
        entity_id=case.id,
        event_type=payment_event_type,
        actor_type=ActorType.SYSTEM,
        payload={
            "provider_reference": payment_request.provider_reference,
            "status": new_status.value,
            "amount_paid": payment_request.amount_paid,
            "payment_id": event.payment_id,
        },
        correlation_id=correlation_id,
    )

    if case.status != RecoveryCaseStatus.EXECUTING:
        # The case already moved on (e.g. a prior event already resolved
        # it) — the event stays claimed, but don't attempt a transition the
        # state machine would reject anyway.
        await session.commit()
        return "ignored"

    attempt = await session.get(RecoveryAttempt, payment_request.recovery_attempt_id)
    fully_paid = is_paid and payment_request.amount_paid >= payment_request.amount

    if fully_paid:
        case.recovered_amount += payment_request.amount_paid
        await _transition(session, case, RecoveryCaseStatus.RECOVERED, correlation_id=correlation_id)
    else:
        failure_category = (
            ProviderFailureCategory.PAYMENT_EXPIRED
            if new_status == RecoveryPaymentRequestStatus.EXPIRED
            else ProviderFailureCategory.PAYMENT_FAILED
        )
        if attempt is not None:
            attempt.failure_code = failure_category.value
            attempt.failure_message = f"Payment link ended in status '{new_status.value}'."
        await _transition(session, case, RecoveryCaseStatus.FAILED, correlation_id=correlation_id)

    await session.commit()
    return "processed"


async def _transition(
    session: AsyncSession, case: RecoveryCase, target: RecoveryCaseStatus, *, correlation_id: str
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
        payload={"from_status": from_status.value, "to_status": target.value, "reason": "webhook_confirmed"},
        correlation_id=correlation_id,
    )
