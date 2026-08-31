from datetime import UTC, datetime

import pytest

from app.domain.enums import RecoveryPaymentRequestStatus
from app.payments.providers.razorpay import (
    RazorpayModeError,
    RazorpayPaymentProvider,
    _from_unix,
    _map_payment_link_status,
    _payment_link_from_response,
    _to_unix,
)


def test_construction_refuses_non_test_mode():
    with pytest.raises(RazorpayModeError):
        RazorpayPaymentProvider(
            key_id="key", key_secret="secret", base_url="https://api.razorpay.com/v1", mode="live",
            timeout_seconds=10.0,
        )


def test_construction_refuses_missing_credentials():
    with pytest.raises(ValueError):
        RazorpayPaymentProvider(
            key_id="", key_secret="", base_url="https://api.razorpay.com/v1", mode="test",
            timeout_seconds=10.0,
        )


def test_construction_succeeds_in_test_mode_with_credentials():
    provider = RazorpayPaymentProvider(
        key_id="key", key_secret="secret", base_url="https://api.razorpay.com/v1", mode="test",
        timeout_seconds=10.0,
    )
    assert provider is not None


def test_to_unix_and_from_unix_round_trip():
    original = datetime(2026, 6, 1, 12, 30, 0, tzinfo=UTC)
    unix_ts = _to_unix(original)
    restored = _from_unix(unix_ts)
    assert restored == original


def test_from_unix_handles_none_and_zero():
    assert _from_unix(None) is None
    assert _from_unix(0) is None


def test_map_payment_link_status_known_values():
    assert _map_payment_link_status("paid") == RecoveryPaymentRequestStatus.PAID
    assert _map_payment_link_status("expired") == RecoveryPaymentRequestStatus.EXPIRED


def test_map_payment_link_status_unknown_fails_closed_to_created():
    # A future Razorpay API change introducing a new status must never be
    # silently treated as "paid".
    assert _map_payment_link_status("some_future_status") == RecoveryPaymentRequestStatus.CREATED


def test_payment_link_from_response_maps_all_fields():
    data = {
        "id": "plink_abc123",
        "short_url": "https://rzp.io/i/xyz",
        "status": "created",
        "amount": 50000,
        "amount_paid": 0,
        "currency": "INR",
        "reference_id": "ref-1",
        "expire_by": 1700000000,
    }
    snapshot = _payment_link_from_response(data)
    assert snapshot.provider_reference == "plink_abc123"
    assert snapshot.short_url == "https://rzp.io/i/xyz"
    assert snapshot.status == RecoveryPaymentRequestStatus.CREATED
    assert snapshot.amount == 50000
    assert snapshot.reference_id == "ref-1"
    assert snapshot.expires_at is not None
