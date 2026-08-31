import pytest

from app.core.errors import InvalidStateTransitionError
from app.domain.enums import RecoveryCaseStatus as Status
from app.domain.state_machine import can_transition, is_terminal, validate_transition


def test_valid_transition_is_allowed():
    assert can_transition(Status.DISCOVERED, Status.ELIGIBLE) is True
    validate_transition(Status.DISCOVERED, Status.ELIGIBLE)  # must not raise


def test_invalid_transition_is_rejected():
    assert can_transition(Status.DISCOVERED, Status.RECOVERED) is False
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(Status.DISCOVERED, Status.RECOVERED)


def test_full_happy_path_is_reachable():
    path = [
        Status.DISCOVERED,
        Status.ELIGIBLE,
        Status.DIAGNOSING,
        Status.RECOMMENDED,
        Status.POLICY_REVIEW,
        Status.APPROVED,
        Status.EXECUTING,
        Status.RECOVERED,
    ]
    for current, target in zip(path, path[1:], strict=False):
        validate_transition(current, target)


@pytest.mark.parametrize(
    "terminal_status", [Status.RECOVERED, Status.STOPPED, Status.ESCALATED, Status.INELIGIBLE]
)
def test_terminal_states_have_no_outgoing_transitions(terminal_status):
    assert is_terminal(terminal_status) is True
    for target in Status:
        assert can_transition(terminal_status, target) is False


def test_non_terminal_states_are_not_terminal():
    for status in [
        Status.DISCOVERED,
        Status.ELIGIBLE,
        Status.DIAGNOSING,
        Status.RECOMMENDED,
        Status.POLICY_REVIEW,
        Status.APPROVED,
        Status.EXECUTING,
        Status.FAILED,
    ]:
        assert is_terminal(status) is False


def test_failed_can_loop_back_to_diagnosing_for_retry():
    validate_transition(Status.FAILED, Status.DIAGNOSING)


def test_every_status_has_a_defined_transition_set():
    from app.domain.state_machine import ALLOWED_TRANSITIONS

    for status in Status:
        assert status in ALLOWED_TRANSITIONS
