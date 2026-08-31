"""Structured, PII-minimal context sent to the AI provider.

Only internal identifiers and behavioral signals are included — never a
customer's name, email, phone number, or raw card details. This module has
no I/O: callers (see `app.services.diagnosis_service`) fetch the ORM rows
and pass already-loaded domain objects in, which keeps `build_context`
trivially unit-testable without a database.
"""
from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.db.base import ensure_utc
from app.domain.enums import FailureReason, PaymentMethodType, RecoveryAction, RecoveryAttemptStatus
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_attempt import RecoveryAttempt


class CustomerPaymentHistory(BaseModel):
    successful_payments: int = Field(ge=0)
    failed_payments: int = Field(ge=0)


class PreviousRecoveryAction(BaseModel):
    action: RecoveryAction
    status: RecoveryAttemptStatus
    created_at: datetime


class PaymentRecoveryContext(BaseModel):
    """Everything the AI is allowed to see for one diagnosis call."""

    payment_id: str
    amount: int
    currency: str
    failure_reason: FailureReason
    payment_method: PaymentMethodType
    attempt_number: int = Field(ge=0)
    customer_payment_history: CustomerPaymentHistory
    previous_recovery_actions: list[PreviousRecoveryAction]
    time_since_failure_minutes: int = Field(ge=0)


def build_context(
    *,
    payment: Payment,
    customer: Customer,
    previous_attempts: list[RecoveryAttempt],
    now: datetime | None = None,
) -> PaymentRecoveryContext:
    reference_time = now or datetime.now(UTC)
    failure_time = ensure_utc(payment.updated_at)
    minutes_since_failure = max(0, int((reference_time - failure_time).total_seconds() // 60))

    successful_payments = max(0, customer.total_payments_count - customer.total_failed_payments_count)

    return PaymentRecoveryContext(
        payment_id=str(payment.id),
        amount=payment.amount,
        currency=payment.currency,
        failure_reason=payment.failure_reason or FailureReason.UNKNOWN,
        payment_method=payment.payment_method_type,
        attempt_number=payment.attempt_number,
        customer_payment_history=CustomerPaymentHistory(
            successful_payments=successful_payments,
            failed_payments=customer.total_failed_payments_count,
        ),
        previous_recovery_actions=[
            PreviousRecoveryAction(
                action=attempt.action, status=attempt.status, created_at=attempt.created_at
            )
            for attempt in previous_attempts
        ],
        time_since_failure_minutes=minutes_since_failure,
    )
