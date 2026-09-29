"""First-in, first-out: try tasks in the order they became ready."""

from __future__ import annotations

from collections.abc import Sequence

from bioflow.domain import Task
from bioflow.scheduling.base_scheduler import Scheduler, SchedulingView


class FifoScheduler(Scheduler):
    """The baseline policy. It ignores priority and deadlines entirely.

    ``TaskGraph.ready_tasks()`` is already in became-ready order, so FIFO keeps it.
    """

    name = "fifo"

    def order(self, ready: Sequence[Task], view: SchedulingView) -> list[Task]:
        return list(ready)
