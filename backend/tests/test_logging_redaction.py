"""Regression coverage for structured-log secret redaction
(app.core.logging). No prior test exercised this module directly —
added during the Phase 4 security review after noticing the original
exact-match redaction list would NOT have caught a field literally named
"webhook_secret" (only "secret" and "key_secret" were listed). Nothing in
the codebase currently logs such a field, but this is exactly the kind
of gap that should be caught by a test, not by inspection alone — see
docs/security.md "Logging".
"""
from __future__ import annotations

import json
import logging

from app.core.logging import JsonFormatter, log_event


def _emit_and_capture(**fields: object) -> dict:
    logger = logging.getLogger("test_logging_redaction")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger.addHandler(_Capture())
    log_event(logger, logging.INFO, "test_event", **fields)
    assert records, "log_event did not emit a record"

    formatted = JsonFormatter().format(records[0])
    return json.loads(formatted)


def test_field_named_secret_is_redacted():
    payload = _emit_and_capture(secret="sk_should_never_appear")
    assert "secret" not in payload


def test_field_named_webhook_secret_is_redacted():
    # The specific gap found during review: "webhook_secret" contains
    # "secret" as a substring but was not an exact match against the old
    # {"password", "secret", "token", "api_key", "authorization",
    # "key_secret"} set.
    payload = _emit_and_capture(webhook_secret="whsec_should_never_appear")
    assert "webhook_secret" not in payload


def test_field_named_razorpay_key_secret_is_redacted():
    payload = _emit_and_capture(razorpay_key_secret="should_never_appear")
    assert "razorpay_key_secret" not in payload


def test_field_named_api_key_is_redacted_case_insensitively():
    payload = _emit_and_capture(API_KEY="should_never_appear")
    assert not any(k.lower() == "api_key" for k in payload)


def test_ordinary_fields_are_not_redacted():
    payload = _emit_and_capture(status_code=200, amount=1500, provider="razorpay")
    assert payload["status_code"] == 200
    assert payload["amount"] == 1500
    assert payload["provider"] == "razorpay"


def test_redacted_value_never_appears_anywhere_in_serialized_output():
    # Belt-and-suspenders: even if a future key name slips past the
    # fragment list, the actual secret VALUE should never leak through
    # some other field by accident in this test's fixture.
    formatted = json.dumps(_emit_and_capture(webhook_secret="unique_marker_xyz123"))
    assert "unique_marker_xyz123" not in formatted
