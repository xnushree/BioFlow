"""Highest experiment priority first."""

from __future__ import annotations

from collections.abc import Sequence

from bioflow.domain import Task
from bioflow.scheduling.base_scheduler import Scheduler, SchedulingView


class PriorityScheduler(Scheduler):
    """Order ready tasks by their experiment's priority (higher first).

    Python's sort is stable, so equal-priority tasks keep FIFO order.
    Known weakness: under sustained high-priority load, low-priority work can
    wait indefinitely (starvation). The benchmarks in Phase 24 measure this.
    """

    name = "priority"

    def order(self, ready: Sequence[Task], view: SchedulingView) -> list[Task]:
        return sorted(ready, key=lambda task: -view.experiment_of(task).priority)
