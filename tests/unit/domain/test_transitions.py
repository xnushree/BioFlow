"""Exhaustive transition tests for plates, tasks and experiments."""

import pytest

from bioflow.core.exceptions import InvalidTransitionError
from bioflow.domain import (
    CultureConditions,
    Experiment,
    ExperimentStatus,
    Operation,
    Plate,
    PlateState,
    Protocol,
    Task,
    TaskStatus,
)
from bioflow.domain.experiment import EXPERIMENT_TRANSITIONS
from bioflow.domain.plate import PLATE_TRANSITIONS
from bioflow.domain.task import TASK_TRANSITIONS

LIVE_PLATE_STATES = ["CREATED", "STORED", "IN_TRANSIT", "WAITING", "INCUBATING", "PROCESSING"]


def test_plate_transitions(assert_exact_transitions) -> None:
    expected = {
        ("CREATED", "STORED"),
        ("STORED", "IN_TRANSIT"), ("STORED", "ARCHIVED"),
        ("IN_TRANSIT", "WAITING"), ("IN_TRANSIT", "STORED"), ("IN_TRANSIT", "DISPOSED"),
        ("WAITING", "INCUBATING"), ("WAITING", "PROCESSING"), ("WAITING", "IN_TRANSIT"),
        ("INCUBATING", "WAITING"),
        ("PROCESSING", "WAITING"),
        ("QUARANTINED", "IN_TRANSIT"), ("QUARANTINED", "WAITING"),
    } | {(state, "QUARANTINED") for state in LIVE_PLATE_STATES}
    assert_exact_transitions(PLATE_TRANSITIONS, expected)


def test_task_transitions(assert_exact_transitions) -> None:
    expected = {
        ("PENDING", "READY"),
        ("READY", "RUNNING"),
        ("RUNNING", "COMPLETED"), ("RUNNING", "FAILED"), ("RUNNING", "READY"),
        ("PENDING", "CANCELLED"), ("READY", "CANCELLED"), ("RUNNING", "CANCELLED"),
    }
    assert_exact_transitions(TASK_TRANSITIONS, expected)


def test_experiment_transitions(assert_exact_transitions) -> None:
    expected = {
        ("SUBMITTED", "RUNNING"),
        ("RUNNING", "COMPLETED"), ("RUNNING", "FAILED"),
        ("SUBMITTED", "CANCELLED"), ("RUNNING", "CANCELLED"),
    }
    assert_exact_transitions(EXPERIMENT_TRANSITIONS, expected)


def test_task_terminal_statuses_match_table() -> None:
    for status in TaskStatus:
        task = Task("T1", "EXP001", "P1", Operation.IMAGE, status=status)
        assert task.is_terminal == TASK_TRANSITIONS.is_terminal(status)


def test_plate_rejects_skipping_transport() -> None:
    plate = Plate("P1", "EXP001", "HEK293", 0.0, CultureConditions(), state=PlateState.STORED)

    with pytest.raises(InvalidTransitionError, match="P1: invalid transition STORED -> INCUBATING"):
        plate.state = PlateState.INCUBATING
    assert plate.state is PlateState.STORED


def test_archived_plate_can_never_change_state() -> None:
    plate = Plate("P1", "EXP001", "HEK293", 0.0, CultureConditions(), state=PlateState.ARCHIVED)

    for state in PlateState:
        if state is not PlateState.ARCHIVED:
            with pytest.raises(InvalidTransitionError):
                plate.state = state


def test_completed_task_cannot_be_restarted() -> None:
    task = Task("T1", "EXP001", "P1", Operation.IMAGE, status=TaskStatus.COMPLETED)

    with pytest.raises(InvalidTransitionError):
        task.status = TaskStatus.RUNNING


def test_experiment_lifecycle(basic_protocol: Protocol) -> None:
    experiment = Experiment("EXP001", basic_protocol, plate_count=1, submitted_at=0.0)

    experiment.status = ExperimentStatus.RUNNING
    experiment.status = ExperimentStatus.COMPLETED

    with pytest.raises(InvalidTransitionError):
        experiment.status = ExperimentStatus.RUNNING
