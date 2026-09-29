"""Tests for Protocol and ProtocolStep invariants."""

import pytest

from bioflow.core.exceptions import ProtocolError
from bioflow.domain import Operation, Protocol, ProtocolStep


def test_valid_protocol_keeps_step_order(basic_protocol: Protocol) -> None:
    assert [step.operation for step in basic_protocol.steps] == [
        Operation.INCUBATE,
        Operation.MEDIA_EXCHANGE,
        Operation.INCUBATE,
        Operation.IMAGE,
        Operation.ARCHIVE,
    ]


def test_steps_list_is_stored_as_tuple() -> None:
    protocol = Protocol("p", "HEK293", [ProtocolStep(Operation.IMAGE)])  # type: ignore[arg-type]

    assert isinstance(protocol.steps, tuple)


def test_incubate_requires_duration() -> None:
    with pytest.raises(ProtocolError, match="requires a duration_min"):
        ProtocolStep(Operation.INCUBATE)


@pytest.mark.parametrize("duration", [0, -5])
def test_duration_must_be_positive(duration: float) -> None:
    with pytest.raises(ProtocolError, match="must be positive"):
        ProtocolStep(Operation.IMAGE, duration)


def test_transport_is_rejected_in_protocols() -> None:
    with pytest.raises(ProtocolError, match="controller plans it"):
        ProtocolStep(Operation.TRANSPORT)


def test_empty_protocol_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="no steps"):
        Protocol("empty", "HEK293", ())


def test_blank_name_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="name"):
        Protocol("  ", "HEK293", (ProtocolStep(Operation.IMAGE),))


def test_terminal_step_must_be_last() -> None:
    steps = (ProtocolStep(Operation.ARCHIVE), ProtocolStep(Operation.IMAGE))

    with pytest.raises(ProtocolError, match="step 1 \\(ARCHIVE\\).*must be the last step"):
        Protocol("bad-order", "HEK293", steps)


def test_protocol_is_immutable(basic_protocol: Protocol) -> None:
    with pytest.raises(AttributeError):
        basic_protocol.name = "changed"  # type: ignore[misc]
