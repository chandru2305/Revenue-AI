"""Explicit recovery-case state machine.

This is the single source of truth for which status transitions are legal.
Services must call `validate_transition` before persisting any status
change — never assign `.status = X` directly. See docs/recovery-state-machine.md
for the rationale behind each edge.
"""
from __future__ import annotations

from app.core.errors import InvalidStateTransitionError
from app.domain.enums import TERMINAL_RECOVERY_CASE_STATUSES
from app.domain.enums import RecoveryCaseStatus as Status

ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.DISCOVERED: frozenset({Status.ELIGIBLE, Status.INELIGIBLE}),
    Status.ELIGIBLE: frozenset({Status.DIAGNOSING, Status.STOPPED}),
    Status.INELIGIBLE: frozenset(),
    Status.DIAGNOSING: frozenset({Status.RECOMMENDED, Status.ESCALATED}),
    Status.RECOMMENDED: frozenset({Status.POLICY_REVIEW}),
    Status.POLICY_REVIEW: frozenset({Status.APPROVED, Status.STOPPED, Status.ESCALATED}),
    Status.APPROVED: frozenset({Status.EXECUTING}),
    Status.EXECUTING: frozenset({Status.RECOVERED, Status.FAILED, Status.ESCALATED}),
    Status.FAILED: frozenset({Status.DIAGNOSING, Status.STOPPED, Status.ESCALATED}),
    Status.RECOVERED: frozenset(),
    Status.STOPPED: frozenset(),
    Status.ESCALATED: frozenset(),
}


def is_terminal(status: Status) -> bool:
    return status in TERMINAL_RECOVERY_CASE_STATUSES


def can_transition(current: Status, target: Status) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def validate_transition(current: Status, target: Status) -> None:
    if not can_transition(current, target):
        raise InvalidStateTransitionError(
            f"Cannot transition recovery case from '{current.value}' to '{target.value}'."
        )
