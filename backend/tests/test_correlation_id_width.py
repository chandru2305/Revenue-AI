"""Regression coverage for the Postgres-only truncation bug: the
orchestrator's per-case correlation id ("<cycle_correlation_id>:<case_id>",
two UUIDs and a colon = 73 chars) exceeded the original 64-char
`correlation_id` columns. SQLite never enforces VARCHAR length, so that
only ever surfaced against a real Postgres database — see
alembic/versions/a1c2f5e9d3b7_widen_correlation_id_columns.py.

These tests are DB-agnostic on purpose: they pin the column width and the
ID-format contract directly, so a regression is caught here even when the
whole suite runs against SQLite.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.audit_event import AuditEvent
from app.models.recovery_attempt import RecoveryAttempt


def _worst_case_case_correlation_id() -> str:
    """Exactly the format `orchestrator_service._process_case` builds:
    `f"{correlation_id}:{case_id}"` with both sides real UUIDs."""
    return f"{uuid.uuid4()}:{uuid.uuid4()}"


def test_worst_case_orchestrator_correlation_id_is_73_chars():
    # Pinned so this test fails loudly if the ID format ever changes shape
    # instead of silently stopping to exercise the bug it guards against.
    assert len(_worst_case_case_correlation_id()) == 73


def test_audit_event_correlation_id_column_fits_the_orchestrator_format():
    column_length = AuditEvent.__table__.c.correlation_id.type.length
    assert column_length >= len(_worst_case_case_correlation_id())


def test_recovery_attempt_correlation_id_column_fits_the_orchestrator_format():
    column_length = RecoveryAttempt.__table__.c.correlation_id.type.length
    assert column_length >= len(_worst_case_case_correlation_id())


@pytest.mark.asyncio
async def test_a_diagnosis_requested_event_with_a_full_length_correlation_id_persists(db_session):
    """A direct model-level proof, independent of which engine runs the
    suite: the longest real correlation id the app produces must actually
    round-trip through this column."""
    from app.domain.enums import ActorType

    long_correlation_id = _worst_case_case_correlation_id()
    event = AuditEvent(
        entity_type="recovery_case",
        entity_id=uuid.uuid4(),
        event_type="diagnosis_requested",
        actor_type=ActorType.SYSTEM,
        payload={"case_status": "discovered"},
        correlation_id=long_correlation_id,
    )
    db_session.add(event)
    await db_session.flush()
    await db_session.refresh(event)

    assert event.correlation_id == long_correlation_id
