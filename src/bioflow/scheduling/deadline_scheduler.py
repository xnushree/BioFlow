"""Earliest Deadline First (EDF)."""

from __future__ import annotations

import math
from collections.abc import Sequence

from bioflow.domain import Task
from bioflow.scheduling.base_scheduler import Scheduler, SchedulingView


class DeadlineScheduler(Scheduler):
    """Order ready tasks by their experiment's deadline (earliest first).

    Experiments without a deadline go last; ties keep FIFO order (stable sort).
    EDF looks only at *when* work is due, not *how much* work remains, so an
    experiment with a later deadline but far more remaining work can still
    finish late. The cost scheduler's critical-ratio term accounts for that.
    """

    name = "deadline"

    def order(self, ready: Sequence[Task], view: SchedulingView) -> list[Task]:
        def deadline(task: Task) -> float:
            value = view.experiment_of(task).deadline
            return math.inf if value is None else value

        return sorted(ready, key=deadline)
