"""Protocol file -> experiment -> tasks -> graph, executed in random valid orders.

The randomised test is the key safety property of Phase 9: whatever order the
scheduler picks ready tasks in, no task ever starts before all of its
dependencies have completed.
"""

import random
from pathlib import Path

import pytest

from bioflow.domain import Experiment, TaskStatus
from bioflow.protocols import load_protocol_library
from bioflow.protocols.task_builder import build_tasks
from bioflow.scheduling.task_graph import TaskGraph

PROTOCOLS = load_protocol_library(Path(__file__).parents[2] / "protocols")


def drain_randomly(graph: TaskGraph, rng: random.Random) -> list[str]:
    """Repeatedly start a random ready task, and complete a random running one."""
    order: list[str] = []
    running: list[str] = []
    now = 0.0
    while graph.ready_tasks() or running:
        ready = graph.ready_tasks()
        if ready and (not running or rng.random() < 0.5):
            chosen = rng.choice(ready)
            for dep in chosen.depends_on:  # the guarantee under test
                assert graph.get(dep).status is TaskStatus.COMPLETED, f"{chosen.task_id} started before {dep}"
            graph.mark_running(chosen.task_id, "EQ", now)
            running.append(chosen.task_id)
        else:
            finished = running.pop(rng.randrange(len(running)))
            graph.mark_completed(finished, now)
            order.append(finished)
        now += 1
    return order


@pytest.mark.parametrize("protocol_name", sorted(PROTOCOLS))
@pytest.mark.parametrize("seed", range(50))
def test_random_execution_respects_dependencies(protocol_name: str, seed: int) -> None:
    experiment = Experiment("EXP001", PROTOCOLS[protocol_name], plate_count=4, submitted_at=0.0)
    graph = TaskGraph()
    graph.add_tasks(build_tasks(experiment))

    order = drain_randomly(graph, random.Random(seed))

    assert len(order) == len(graph)
    assert graph.is_experiment_finished("EXP001")
    assert all(t.status is TaskStatus.COMPLETED for t in graph)


def test_synchronized_imaging_waits_for_every_plate() -> None:
    experiment = Experiment("EXP001", PROTOCOLS["imaging_experiment"], plate_count=3, submitted_at=0.0)
    graph = TaskGraph()
    graph.add_tasks(build_tasks(experiment))

    # Finish incubation (step 1) for two of the three plates.
    for plate in ("EXP001-P001", "EXP001-P002"):
        graph.mark_running(f"{plate}-S01", "INCUBATOR_01", 0)
        graph.mark_completed(f"{plate}-S01", 360)

    assert all(t.task_id.endswith("-S01") for t in graph.ready_tasks())  # no IMAGE yet

    graph.mark_running("EXP001-P003-S01", "INCUBATOR_01", 0)
    released = graph.mark_completed("EXP001-P003-S01", 365)

    assert sorted(t.task_id for t in released) == ["EXP001-P001-S02", "EXP001-P002-S02", "EXP001-P003-S02"]


def test_large_experiment_builds_quickly() -> None:
    experiment = Experiment("BIG", PROTOCOLS["basic_experiment"], plate_count=5000, submitted_at=0.0)
    graph = TaskGraph()

    graph.add_tasks(build_tasks(experiment))

    assert len(graph) == 25_000
    assert len(graph.ready_tasks()) == 5000
