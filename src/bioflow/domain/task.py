"""Tasks: the schedulable unit of work (one operation on one plate)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bioflow.core.validation import require
from bioflow.domain.operation import Operation


class TaskStatus(StrEnum):
    PENDING = "PENDING"  # dependencies not yet complete
    READY = "READY"  # dependencies complete, waiting for resources
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


@dataclass(eq=False)
class Task:
    """One operation on one plate, plus the IDs of tasks that must finish first.

    Dependencies are stored as task IDs rather than object references so tasks
    stay easy to persist (Phase 20) and serialise over the API (Phase 21).
    """

    task_id: str
    experiment_id: str
    plate_id: str
    operation: Operation
    depends_on: frozenset[str] = frozenset()
    duration_min: float | None = None
    status: TaskStatus = TaskStatus.PENDING
    assigned_equipment_id: str | None = None
    started_at: float | None = None
    completed_at: float | None = None

    def __post_init__(self) -> None:
        self.depends_on = frozenset(self.depends_on)
        require(bool(self.task_id.strip()), "task_id must not be empty")
        require(self.task_id not in self.depends_on, f"{self.task_id} cannot depend on itself")
        require(
            self.duration_min is None or self.duration_min > 0,
            f"{self.task_id}: duration_min must be positive, got {self.duration_min}",
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES
