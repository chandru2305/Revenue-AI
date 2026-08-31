"""AuditEvent model — append-only record of every notable decision/action.

No foreign key to a single entity table: an audit event can reference a
Payment, RecoveryCase, or RecoveryAttempt, identified generically by
(entity_type, entity_id). This is a deliberate trade-off — it keeps the
audit trail decoupled from any one entity's schema so it can never block or
be blocked by domain migrations.

Repositories must expose only `create`/`list` for this table — never
`update` or `delete`. History is immutable.
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.domain.enums import ActorType


class AuditEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_events"

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_type: Mapped[ActorType] = mapped_column(String(16), nullable=False)

    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditEvent id={self.id} event_type={self.event_type}>"
