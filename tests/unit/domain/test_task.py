"""Tests for Task."""

import pytest

from bioflow.core.exceptions import ValidationError
from bioflow.domain import Operation, Task, TaskStatus


def make_task(**overrides: object) -> Task:
    fields: dict[str, object] = {
        "task_id": "T2",
        "experiment_id": "EXP001",
        "plate_id": "EXP001-P001",
        "operation": Operation.MEDIA_EXCHANGE,
    }
    fields.update(overrides)
    return Task(**fields)  # type: ignore[arg-type]


def test_new_task_is_pending_and_unassigned() -> None:
    task = make_task()

    assert task.status is TaskStatus.PENDING
    assert task.assigned_equipment_id is None
    assert task.depends_on == frozenset()


def test_dependencies_are_normalised_to_frozenset() -> None:
    task = make_task(depends_on=["T1", "T1", "T0"])

    assert task.depends_on == frozenset({"T0", "T1"})


def test_task_cannot_depend_on_itself() -> None:
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        make_task(task_id="T2", depends_on={"T2"})


def test_duration_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        make_task(duration_min=0)


@pytest.mark.parametrize(
    ("status", "terminal"),
    [
        (TaskStatus.PENDING, False),
        (TaskStatus.READY, False),
        (TaskStatus.RUNNING, False),
        (TaskStatus.COMPLETED, True),
        (TaskStatus.FAILED, True),
        (TaskStatus.CANCELLED, True),
    ],
)
def test_terminal_statuses(status: TaskStatus, terminal: bool) -> None:
    assert make_task(status=status).is_terminal is terminal
