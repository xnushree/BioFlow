"""Tests for TaskGraph."""

import pytest

from bioflow.core.exceptions import InvalidTransitionError, UnknownEntityError, ValidationError
from bioflow.domain import Operation, Task, TaskStatus
from bioflow.scheduling.task_graph import TaskGraph


def task(task_id: str, *deps: str, status: TaskStatus = TaskStatus.PENDING) -> Task:
    return Task(task_id, "EXP001", "P1", Operation.IMAGE, depends_on=frozenset(deps), status=status)


def ready_ids(graph: TaskGraph) -> list[str]:
    return [t.task_id for t in graph.ready_tasks()]


def run(graph: TaskGraph, task_id: str, now: float = 0.0) -> list[str]:
    """Start and complete a task; return IDs that became ready."""
    graph.mark_running(task_id, "EQ", now)
    return [t.task_id for t in graph.mark_completed(task_id, now + 1)]


@pytest.fixture
def diamond() -> TaskGraph:
    """A -> (B, C) -> D : branching then joining."""
    graph = TaskGraph()
    graph.add_tasks([task("A"), task("B", "A"), task("C", "A"), task("D", "B", "C")])
    return graph


def test_tasks_without_dependencies_start_ready(diamond: TaskGraph) -> None:
    assert ready_ids(diamond) == ["A"]
    assert diamond.get("D").status is TaskStatus.PENDING


def test_add_returns_immediately_ready_tasks() -> None:
    assert [t.task_id for t in TaskGraph().add_tasks([task("A"), task("B", "A")])] == ["A"]


def test_completion_releases_branches(diamond: TaskGraph) -> None:
    assert run(diamond, "A") == ["B", "C"]
    assert ready_ids(diamond) == ["B", "C"]


def test_join_waits_for_every_dependency(diamond: TaskGraph) -> None:
    run(diamond, "A")

    assert run(diamond, "B") == []
    assert diamond.get("D").status is TaskStatus.PENDING
    assert run(diamond, "C") == ["D"]


def test_running_task_leaves_ready_list_and_records_assignment(diamond: TaskGraph) -> None:
    diamond.mark_running("A", "IMAGING_01", now=5.0)

    a = diamond.get("A")
    assert ready_ids(diamond) == []
    assert (a.status, a.assigned_equipment_id, a.started_at) == (TaskStatus.RUNNING, "IMAGING_01", 5.0)


def test_cannot_run_a_task_that_is_not_ready(diamond: TaskGraph) -> None:
    with pytest.raises(InvalidTransitionError, match="PENDING -> RUNNING"):
        diamond.mark_running("D", "EQ", 0.0)


def test_cannot_complete_a_task_that_is_not_running(diamond: TaskGraph) -> None:
    with pytest.raises(InvalidTransitionError):
        diamond.mark_completed("A", 0.0)
    assert ready_ids(diamond) == ["A"]  # index untouched


def test_requeue_returns_interrupted_task_to_ready(diamond: TaskGraph) -> None:
    diamond.mark_running("A", "INCUBATOR_01", 0.0)

    diamond.requeue("A")

    a = diamond.get("A")
    assert ready_ids(diamond) == ["A"]
    assert (a.status, a.assigned_equipment_id, a.started_at) == (TaskStatus.READY, None, None)


def test_failure_cancels_everything_downstream(diamond: TaskGraph) -> None:
    run(diamond, "A")
    diamond.mark_running("B", "EQ", 1.0)

    cancelled = diamond.mark_failed("B", 2.0)

    assert [t.task_id for t in cancelled] == ["D"]
    assert diamond.get("C").status is TaskStatus.READY  # a sibling branch is unaffected
    assert ready_ids(diamond) == ["C"]


def test_cancel_removes_task_and_descendants(diamond: TaskGraph) -> None:
    cancelled = diamond.cancel("A")

    assert [t.task_id for t in cancelled] == ["A", "B", "C", "D"]
    assert ready_ids(diamond) == []
    assert diamond.is_experiment_finished("EXP001")


def test_duplicate_ids_rejected() -> None:
    graph = TaskGraph()
    graph.add_tasks([task("A")])

    with pytest.raises(ValidationError, match=r"Duplicate task IDs: \['A', 'B'\]"):
        graph.add_tasks([task("A"), task("B"), task("B")])


def test_unknown_dependency_rejected() -> None:
    with pytest.raises(ValidationError, match="B depends on unknown task GHOST"):
        TaskGraph().add_tasks([task("A"), task("B", "GHOST")])


def test_cycle_rejected_and_graph_unchanged() -> None:
    graph = TaskGraph()
    graph.add_tasks([task("START")])

    with pytest.raises(ValidationError, match=r"Dependency cycle among tasks: \['X', 'Y', 'Z'\]"):
        graph.add_tasks([task("X", "Z"), task("Y", "X"), task("Z", "Y"), task("OK", "START")])

    assert len(graph) == 1  # atomic: nothing from the failed batch was added
    assert "OK" not in graph


def test_new_tasks_can_depend_on_completed_ones() -> None:
    graph = TaskGraph()
    graph.add_tasks([task("A")])
    run(graph, "A")

    assert [t.task_id for t in graph.add_tasks([task("B", "A")])] == ["B"]


def test_new_tasks_cannot_depend_on_failed_ones() -> None:
    graph = TaskGraph()
    graph.add_tasks([task("A")])
    graph.mark_running("A", "EQ", 0)
    graph.mark_failed("A", 1)

    with pytest.raises(ValidationError, match="FAILED and will never complete"):
        graph.add_tasks([task("B", "A")])


def test_progress_and_completion(diamond: TaskGraph) -> None:
    for task_id in ("A", "B", "C"):
        run(diamond, task_id)
    assert diamond.progress("EXP001") == {TaskStatus.COMPLETED: 3, TaskStatus.READY: 1}
    assert not diamond.is_experiment_finished("EXP001")

    run(diamond, "D")

    assert diamond.is_experiment_finished("EXP001")
    assert not diamond.is_experiment_finished("NO_SUCH_EXPERIMENT")


def test_unknown_task_lookup(diamond: TaskGraph) -> None:
    with pytest.raises(UnknownEntityError, match="task"):
        diamond.get("NOPE")


def test_dependents_query(diamond: TaskGraph) -> None:
    assert [t.task_id for t in diamond.dependents("A")] == ["B", "C"]
