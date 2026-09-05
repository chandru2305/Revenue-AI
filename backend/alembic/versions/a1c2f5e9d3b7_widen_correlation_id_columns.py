"""Widen correlation_id columns to 128 chars

The orchestrator scopes a case's events to
"<cycle_correlation_id>:<case_id>" — two UUIDs plus a colon, 73
characters — which exceeded the original 64-char limit on
`audit_events.correlation_id` and `recovery_attempts.correlation_id`.
SQLite doesn't enforce VARCHAR length, so this only ever surfaced as a
`StringDataRightTruncationError` against a real (Postgres) database, the
first time an orchestrator cycle actually diagnosed or executed a case.

128 matches the ceiling `app.main._CORRELATION_ID_MAX_LENGTH` already
uses for inbound correlation IDs, so every correlation ID the app can
receive or generate now fits in every column that stores one.

Revision ID: a1c2f5e9d3b7
Revises: f3608d69ea91
Create Date: 2026-09-05 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c2f5e9d3b7"
down_revision: str | None = "f3608d69ea91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.alter_column(
            "correlation_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=True,
        )
    with op.batch_alter_table("recovery_attempts") as batch_op:
        batch_op.alter_column(
            "correlation_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Narrowing back to 64 would truncate any "<uuid>:<uuid>" correlation
    # ID an orchestrator cycle has already written — deliberately a no-op
    # rather than a silent data-loss downgrade. Restore the strict 64-char
    # type by hand only after confirming no such row exists.
    pass
