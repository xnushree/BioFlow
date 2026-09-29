"""Expand an Experiment (protocol x plates) into dependent Tasks."""

from __future__ import annotations

from bioflow.domain import Experiment, Task


def task_id(plate_id: str, step_number: int) -> str:
    """Deterministic ID, e.g. ``task_id("EXP001-P003", 2) == "EXP001-P003-S02"``."""
    return f"{plate_id}-S{step_number:02d}"


def build_tasks(experiment: Experiment) -> list[Task]:
    """One task per (plate, protocol step).

    Dependencies:
        * normal step k of a plate depends on that plate's step k-1 (a chain);
        * a ``synchronize`` step k depends on step k-1 of *every* plate in the
          experiment (a join), so all plates reach that point before any proceeds.

    Tasks are returned in step-major order (all plates' step 1, then step 2, ...),
    which is also a valid topological order.
    """
    plate_ids = [plate.plate_id for plate in experiment.create_plates()]
    tasks: list[Task] = []
    for number, step in enumerate(experiment.protocol.steps, start=1):
        previous = number - 1
        for plate_id in plate_ids:
            if previous == 0:
                depends_on: frozenset[str] = frozenset()
            elif step.synchronize:
                depends_on = frozenset(task_id(other, previous) for other in plate_ids)
            else:
                depends_on = frozenset({task_id(plate_id, previous)})
            tasks.append(Task(
                task_id=task_id(plate_id, number),
                experiment_id=experiment.experiment_id,
                plate_id=plate_id,
                operation=step.operation,
                depends_on=depends_on,
                duration_min=step.duration_min,
            ))
    return tasks
