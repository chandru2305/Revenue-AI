import hashlib
import hmac

from app.payments.webhooks import parse_event, verify_signature


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_verify_signature_accepts_correctly_signed_body():
    body = b'{"event": "payment_link.paid"}'
    secret = "whsec_test123"
    signature = _sign(body, secret)
    assert verify_signature(body, signature, secret) is True


def test_verify_signature_rejects_wrong_signature():
    body = b'{"event": "payment_link.paid"}'
    assert verify_signature(body, "0" * 64, "whsec_test123") is False


def test_verify_signature_rejects_tampered_body():
    secret = "whsec_test123"
    original_body = b'{"event": "payment_link.paid", "amount": 100}'
    signature = _sign(original_body, secret)
    tampered_body = b'{"event": "payment_link.paid", "amount": 999999}'
    assert verify_signature(tampered_body, signature, secret) is False


def test_verify_signature_rejects_missing_secret_or_signature():
    body = b"{}"
    assert verify_signature(body, "somesig", "") is False
    assert verify_signature(body, "", "somesecret") is False


def test_parse_event_extracts_payment_link_fields():
    payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {"id": "plink_abc123", "status": "paid", "amount_paid": 50000}
            },
            "payment": {"entity": {"id": "pay_xyz789"}},
        },
    }
    event = parse_event(payload)
    assert event.event_type == "payment_link.paid"
    assert event.payment_link_id == "plink_abc123"
    assert event.payment_link_status == "paid"
    assert event.amount_paid == 50000
    assert event.payment_id == "pay_xyz789"
    assert event.dedup_key == "payment_link.paid:plink_abc123:pay_xyz789"


def test_parse_event_handles_missing_payment_entity():
    payload = {
        "event": "payment_link.expired",
        "payload": {"payment_link": {"entity": {"id": "plink_abc123", "status": "expired"}}},
    }
    event = parse_event(payload)
    assert event.payment_id is None
    assert event.dedup_key == "payment_link.expired:plink_abc123:"


def test_parse_event_handles_completely_empty_payload():
    event = parse_event({})
    assert event.event_type == ""
    assert event.payment_link_id is None
    assert event.dedup_key == "::"


def test_provider_event_id_is_preferred_over_the_payload_derived_key():
    payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {"entity": {"id": "plink_abc123", "status": "paid", "amount_paid": 50000}},
            "payment": {"entity": {"id": "pay_xyz789"}},
        },
    }
    event = parse_event(payload, event_id="evt_00000001")
    assert event.dedup_key == "event_id:evt_00000001"
    assert event.dedup_key_source == "event_id"
    # Everything else still parses identically.
    assert event.payment_link_id == "plink_abc123"
    assert event.payment_link_status == "paid"
    assert event.amount_paid == 50000


def test_falls_back_to_the_payload_key_when_no_event_id_header():
    payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {"entity": {"id": "plink_abc123", "status": "paid"}},
            "payment": {"entity": {"id": "pay_xyz789"}},
        },
    }
    for missing in (None, ""):
        event = parse_event(payload, event_id=missing)
        assert event.dedup_key == "payment_link.paid:plink_abc123:pay_xyz789"
        assert event.dedup_key_source == "payload"


def test_same_event_id_beats_a_differing_payload():
    """A redelivery whose body differs (different payment entity) must
    still dedup to the same key when the provider event id matches."""
    a = parse_event({"event": "payment_link.paid"}, event_id="evt_same")
    b = parse_event(
        {
            "event": "payment_link.paid",
            "payload": {"payment": {"entity": {"id": "pay_different"}}},
        },
        event_id="evt_same",
    )
    assert a.dedup_key == b.dedup_key


def test_distinct_event_ids_produce_distinct_keys():
    payload = {"event": "payment_link.paid"}
    assert parse_event(payload, event_id="evt_1").dedup_key != parse_event(
        payload, event_id="evt_2"
    ).dedup_key


def test_two_deliveries_of_the_same_event_produce_the_same_dedup_key():
    payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {"entity": {"id": "plink_same", "status": "paid", "amount_paid": 100}},
            "payment": {"entity": {"id": "pay_same"}},
        },
    }
    first = parse_event(payload)
    second = parse_event(dict(payload))  # simulates a second, independent delivery
    assert first.dedup_key == second.dedup_key
