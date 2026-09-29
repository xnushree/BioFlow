"""Tests for the BioFlow-X exception hierarchy."""

import pytest

from bioflow.core.exceptions import (
    BioFlowError,
    CapacityExceededError,
    ConfigurationError,
    DuplicateAllocationError,
    InvalidTransitionError,
    ProtocolError,
    ResourceError,
    ResourceUnavailableError,
    SimulationError,
    UnknownEntityError,
    UnrecoverableFaultError,
    ValidationError,
)


@pytest.mark.parametrize(
    "error",
    [
        ConfigurationError("bad config"),
        ValidationError("negative duration"),
        ProtocolError("bad protocol"),
        SimulationError("event in the past"),
        UnknownEntityError("ROBOT_99", "robot"),
        InvalidTransitionError("ROBOT_01", "IDLE", "PLACING"),
        ResourceUnavailableError("IMAGING_01", "faulted"),
        CapacityExceededError("INCUBATOR_01", 120),
        DuplicateAllocationError("ROBOT_01", "TASK_7"),
        UnrecoverableFaultError("F001", "no backup incubator"),
    ],
)
def test_every_error_is_a_bioflow_error(error: BioFlowError) -> None:
    assert isinstance(error, BioFlowError)


def test_resource_errors_share_a_base_class() -> None:
    for error in (
        ResourceUnavailableError("X", "offline"),
        CapacityExceededError("X", 1),
        DuplicateAllocationError("X", "T1"),
    ):
        assert isinstance(error, ResourceError)
        assert error.resource_id == "X"


def test_invalid_transition_carries_structured_details() -> None:
    error = InvalidTransitionError("ROBOT_01", "IDLE", "PLACING")

    assert (error.entity_id, error.from_state, error.to_state) == ("ROBOT_01", "IDLE", "PLACING")
    assert str(error) == "ROBOT_01: invalid transition IDLE -> PLACING"


def test_capacity_error_message_includes_capacity() -> None:
    error = CapacityExceededError("INCUBATOR_02", 120)

    assert error.capacity == 120
    assert str(error) == "INCUBATOR_02: capacity 120 exceeded"


def test_unknown_entity_message_names_the_kind() -> None:
    assert str(UnknownEntityError("P017", "plate")) == "Unknown plate: 'P017'"
