"""Declarative base and shared mixins for all ORM models."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Normalizes a possibly-naive datetime to UTC-aware.

    SQLite (used for local dev/tests) doesn't actually persist timezone
    info even for a `DateTime(timezone=True)` column — a value round-tripped
    through it can come back naive, while the same column on PostgreSQL
    (the source-of-truth backend) stays aware. Every value this app writes
    is UTC (`utcnow()` above), so treating a naive value as UTC here is
    correct, not a guess. Any code doing arithmetic on an ORM-loaded
    timestamp should call this first.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
