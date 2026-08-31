import uuid
from datetime import UTC, datetime, timedelta

from app.ai.context import build_context
from app.domain.enums import (
    FailureReason,
    PaymentMethodType,
    PaymentStatus,
    RecoveryAction,
    RecoveryAttemptStatus,
)
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_attempt import RecoveryAttempt


def _payment(**overrides) -> Payment:
    now = datetime.now(UTC)
    defaults = dict(
        id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        amount=15_000,
        currency="INR",
        status=PaymentStatus.FAILED,
        payment_method_type=PaymentMethodType.CARD,
        failure_reason=FailureReason.NETWORK_ERROR,
        attempt_number=1,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Payment(**defaults)


def _customer(**overrides) -> Customer:
    defaults = dict(
        id=uuid.uuid4(), total_payments_count=5, total_failed_payments_count=1, total_recovered_amount=0
    )
    defaults.update(overrides)
    return Customer(**defaults)


def test_build_context_maps_fields_directly():
    payment = _payment()
    customer = _customer()

    context = build_context(payment=payment, customer=customer, previous_attempts=[])

    assert context.payment_id == str(payment.id)
    assert context.amount == payment.amount
    assert context.currency == "INR"
    assert context.failure_reason == FailureReason.NETWORK_ERROR
    assert context.payment_method == PaymentMethodType.CARD
    assert context.attempt_number == 1
    assert context.previous_recovery_actions == []


def test_build_context_derives_success_failure_counts():
    customer = _customer(total_payments_count=10, total_failed_payments_count=3)

    context = build_context(payment=_payment(), customer=customer, previous_attempts=[])

    assert context.customer_payment_history.successful_payments == 7
    assert context.customer_payment_history.failed_payments == 3


def test_build_context_never_produces_negative_successful_payments():
    # Defensive: shouldn't happen given the model's invariants, but the
    # context builder must not emit a negative count regardless.
    customer = _customer(total_payments_count=2, total_failed_payments_count=5)

    context = build_context(payment=_payment(), customer=customer, previous_attempts=[])

    assert context.customer_payment_history.successful_payments == 0


def test_build_context_computes_minutes_since_failure():
    failure_time = datetime.now(UTC) - timedelta(minutes=42)
    payment = _payment(updated_at=failure_time)
    reference_time = failure_time + timedelta(minutes=42)

    context = build_context(
        payment=payment, customer=_customer(), previous_attempts=[], now=reference_time
    )

    assert context.time_since_failure_minutes == 42


def test_build_context_falls_back_to_unknown_failure_reason_when_none():
    payment = _payment(failure_reason=None)

    context = build_context(payment=payment, customer=_customer(), previous_attempts=[])

    assert context.failure_reason == FailureReason.UNKNOWN


def test_build_context_includes_previous_recovery_actions():
    now = datetime.now(UTC)
    attempt = RecoveryAttempt(
        id=uuid.uuid4(),
        recovery_case_id=uuid.uuid4(),
        action=RecoveryAction.RETRY_PAYMENT,
        status=RecoveryAttemptStatus.FAILED,
        created_at=now,
    )

    context = build_context(payment=_payment(), customer=_customer(), previous_attempts=[attempt])

    assert len(context.previous_recovery_actions) == 1
    assert context.previous_recovery_actions[0].action == RecoveryAction.RETRY_PAYMENT
    assert context.previous_recovery_actions[0].status == RecoveryAttemptStatus.FAILED


def test_context_serializes_no_direct_customer_identifiers():
    # The context must never carry a customer name/email/phone — only the
    # opaque payment id and aggregate counters.
    context = build_context(payment=_payment(), customer=_customer(), previous_attempts=[])
    payload = context.model_dump(mode="json")

    assert set(payload.keys()) == {
        "payment_id",
        "amount",
        "currency",
        "failure_reason",
        "payment_method",
        "attempt_number",
        "customer_payment_history",
        "previous_recovery_actions",
        "time_since_failure_minutes",
    }
