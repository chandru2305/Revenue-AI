"""Audit trail export — CSV/JSON downloads of the append-only audit trail.

Read-only: this module only ever queries `AuditEvent` rows (via
`AuditEventRepository.list_for_export`, which has no write path) and never
mutates them. Nothing here can modify the trail it exports.

Every exported column is either a base `AuditEvent` column (`timestamp`,
`entity_type`, `entity_id`, `event_type`, `actor_type`, `correlation_id`)
or a value defensively pulled from that event's own `payload` — never a
value invented for a shape the event doesn't carry. `payload_json` carries
the full (redacted) payload so nothing the fixed columns can't express is
lost.

No secret ever reaches this export: `AuditEvent.payload` is built entirely
by `app.services.audit_service.record_event` call sites, none of which
ever pass a credential (see docs/security.md), and `_redact` re-checks
every payload key against the same substring filter
`app.core.logging` uses for log lines, as defense in depth against a
future call site accidentally doing so.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import is_redacted_key
from app.models.audit_event import AuditEvent
from app.repositories.audit_event_repository import AuditEventRepository

# Fixed column order for both CSV and JSON export, so the two formats carry
# the same information. Columns beyond the base AuditEvent fields are
# derived from `payload` when present, blank/omitted otherwise — never
# invented for an event type that doesn't carry them.
EXPORT_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "entity_type",
    "entity_id",
    "event_type",
    "actor_type",
    "correlation_id",
    "case_id",
    "payment_id",
    "from_status",
    "to_status",
    "reason",
    "policy_decision",
    "policy_reason_codes",
    "policy_version",
    "proposed_action",
    "recommended_action",
    "recovery_confidence",
    "diagnosis_category",
    "ai_model",
    "ai_decision_source",
    "ai_latency_ms",
    "amount",
    "currency",
    "provider_reference",
    "short_url",
    "final_outcome",
    "payload_json",
)


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop any key that looks like a credential, recursively. Belt and
    suspenders — no current call site puts one in an audit payload."""
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if is_redacted_key(key):
            continue
        cleaned[key] = _redact(value) if isinstance(value, dict) else value
    return cleaned


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


_TERMINAL_STATUSES = frozenset({"recovered", "stopped", "escalated", "failed", "ineligible"})


def _row_for(event: AuditEvent) -> dict[str, Any]:
    payload = event.payload or {}
    to_status = _first(payload, "to_status")
    final_outcome = to_status if to_status in _TERMINAL_STATUSES else None

    case_id = str(event.entity_id) if event.entity_type == "recovery_case" else None

    return {
        "timestamp": event.created_at.isoformat(),
        "entity_type": event.entity_type,
        "entity_id": str(event.entity_id),
        "event_type": event.event_type,
        "actor_type": event.actor_type.value if hasattr(event.actor_type, "value") else event.actor_type,
        "correlation_id": event.correlation_id or "",
        "case_id": case_id or "",
        "payment_id": _first(payload, "payment_id") or "",
        "from_status": _first(payload, "from_status") or "",
        "to_status": to_status or "",
        "reason": _first(payload, "reason", "decision_explanation", "error") or "",
        "policy_decision": _first(payload, "decision") or "",
        "policy_reason_codes": ",".join(payload.get("reason_codes") or []),
        "policy_version": _first(payload, "policy_version") or "",
        "proposed_action": _first(payload, "proposed_action") or "",
        "recommended_action": _first(payload, "recommended_action") or "",
        "recovery_confidence": _first(payload, "recovery_confidence"),
        "diagnosis_category": _first(payload, "diagnosis_category") or "",
        "ai_model": _first(payload, "model") or "",
        "ai_decision_source": _first(payload, "decision_source") or "",
        "ai_latency_ms": _first(payload, "latency_ms"),
        "amount": _first(payload, "amount_paid", "amount", "revenue_at_risk"),
        "currency": _first(payload, "currency") or "",
        "provider_reference": _first(payload, "provider_reference") or "",
        "short_url": _first(payload, "short_url") or "",
        "final_outcome": final_outcome or "",
        "payload_json": json.dumps(_redact(payload), default=str),
    }


async def fetch_export_rows(
    session: AsyncSession,
    *,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    event_type: str | None = None,
    correlation_id: str | None = None,
) -> list[dict[str, Any]]:
    """Every matching audit event, oldest first, flattened into export rows."""
    repo = AuditEventRepository(session)
    events = await repo.list_for_export(
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        correlation_id=correlation_id,
    )
    return [_row_for(event) for event in events]


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_COLUMNS), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def rows_to_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, default=str, indent=2)
