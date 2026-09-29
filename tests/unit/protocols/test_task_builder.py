"""Tests for expanding experiments into tasks."""

import pytest

from bioflow.core.exceptions import ProtocolError
from bioflow.domain import Experiment, Operation, Protocol, ProtocolStep
from bioflow.protocols import validate_protocol
from bioflow.protocols.task_builder import build_tasks, task_id


def make_experiment(steps: tuple[ProtocolStep, ...], plates: int) -> Experiment:
    return Experiment("EXP001", Protocol("p", "HEK293", steps), plate_count=plates, submitted_at=0.0)


LINEAR = (
    ProtocolStep(Operation.INCUBATE, 720),
    ProtocolStep(Operation.MEDIA_EXCHANGE),
    ProtocolStep(Operation.ARCHIVE),
)


def test_one_task_per_plate_and_step() -> None:
    tasks = build_tasks(make_experiment(LINEAR, plates=3))

    assert len(tasks) == 9
    assert tasks[0].task_id == "EXP001-P001-S01"
    assert {t.plate_id for t in tasks} == {"EXP001-P001", "EXP001-P002", "EXP001-P003"}


def test_each_plate_is_a_chain() -> None:
    tasks = {t.task_id: t for t in build_tasks(make_experiment(LINEAR, plates=2))}

    assert tasks["EXP001-P001-S01"].depends_on == frozenset()
    assert tasks["EXP001-P001-S02"].depends_on == {"EXP001-P001-S01"}
    assert tasks["EXP001-P002-S03"].depends_on == {"EXP001-P002-S02"}


def test_tasks_copy_operation_and_duration() -> None:
    tasks = build_tasks(make_experiment(LINEAR, plates=1))

    assert [(t.operation, t.duration_min, t.step) for t in tasks] == [
        (Operation.INCUBATE, 720, 1), (Operation.MEDIA_EXCHANGE, None, 2), (Operation.ARCHIVE, None, 3)
    ]


def test_synchronized_step_joins_all_plates() -> None:
    steps = (
        ProtocolStep(Operation.INCUBATE, 60),
        ProtocolStep(Operation.IMAGE, synchronize=True),
        ProtocolStep(Operation.ARCHIVE),
    )
    tasks = {t.task_id: t for t in build_tasks(make_experiment(steps, plates=3))}

    all_incubations = {task_id(f"EXP001-P00{n}", 1) for n in (1, 2, 3)}
    for n in (1, 2, 3):
        assert tasks[task_id(f"EXP001-P00{n}", 2)].depends_on == all_incubations
    # After the join, each plate continues on its own chain.
    assert tasks["EXP001-P002-S03"].depends_on == {"EXP001-P002-S02"}


def test_tasks_are_in_topological_order() -> None:
    seen: set[str] = set()
    for task in build_tasks(make_experiment(LINEAR, plates=4)):
        assert task.depends_on <= seen
        seen.add(task.task_id)


def test_first_step_cannot_synchronize() -> None:
    with pytest.raises(ProtocolError, match="step 1 cannot synchronize"):
        Protocol("p", "HEK293", (ProtocolStep(Operation.IMAGE, synchronize=True), ProtocolStep(Operation.ARCHIVE)))


def test_validator_accepts_synchronize_flag_and_rejects_non_bool() -> None:
    base = {"protocol": "p", "cell_type": "c"}
    ok = validate_protocol({**base, "steps": [
        {"operation": "INCUBATE", "duration_min": 5}, {"operation": "IMAGE", "synchronize": True},
        {"operation": "ARCHIVE"},
    ]})
    bad = validate_protocol({**base, "steps": [
        {"operation": "INCUBATE", "duration_min": 5}, {"operation": "IMAGE", "synchronize": "yes"},
        {"operation": "ARCHIVE"},
    ]})

    assert ok.protocol is not None and ok.protocol.steps[1].synchronize
    assert [str(i) for i in bad.issues] == ["step 2.synchronize: expected true or false, got 'yes'"]
