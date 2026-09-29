"""Experiments: a protocol applied to a batch of plates, with a priority and deadline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bioflow.core.state_machine import TransitionGuard, TransitionTable
from bioflow.core.validation import require
from bioflow.domain.plate import Plate
from bioflow.domain.protocol import Protocol


class ExperimentStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


EXPERIMENT_TRANSITIONS = TransitionTable.build(
    ExperimentStatus,
    {
        ExperimentStatus.SUBMITTED: {ExperimentStatus.RUNNING},
        ExperimentStatus.RUNNING: {ExperimentStatus.COMPLETED, ExperimentStatus.FAILED},
    },
    from_any={ExperimentStatus.CANCELLED},
    terminal={ExperimentStatus.COMPLETED, ExperimentStatus.FAILED, ExperimentStatus.CANCELLED},
)


@dataclass(eq=False)
class Experiment(TransitionGuard):
    """A request to run ``protocol`` on ``plate_count`` plates.

    ``priority``: higher number = more urgent. ``deadline``: absolute simulation
    time (minutes) by which every plate should finish, or ``None`` for no deadline.

    Assigning ``experiment.status`` is validated against ``EXPERIMENT_TRANSITIONS``.
    """

    _state_attr = "status"
    _id_attr = "experiment_id"
    _transitions = EXPERIMENT_TRANSITIONS

    experiment_id: str
    protocol: Protocol
    plate_count: int
    submitted_at: float
    priority: int = 0
    deadline: float | None = None
    status: ExperimentStatus = ExperimentStatus.SUBMITTED

    def __post_init__(self) -> None:
        require(bool(self.experiment_id.strip()), "experiment_id must not be empty")
        require(self.plate_count >= 1, f"{self.experiment_id}: plate_count must be >= 1")
        require(self.submitted_at >= 0, f"{self.experiment_id}: submitted_at must be >= 0")
        require(self.priority >= 0, f"{self.experiment_id}: priority must be >= 0")
        require(
            self.deadline is None or self.deadline > self.submitted_at,
            f"{self.experiment_id}: deadline must be after submitted_at",
        )

    def create_plates(self) -> list[Plate]:
        """Create this experiment's plates with IDs like ``EXP001-P001``."""
        return [
            Plate(
                plate_id=f"{self.experiment_id}-P{number:03d}",
                experiment_id=self.experiment_id,
                cell_type=self.protocol.cell_type,
                created_at=self.submitted_at,
                conditions=self.protocol.conditions,
            )
            for number in range(1, self.plate_count + 1)
        ]

    def is_late(self, now: float) -> bool:
        return self.deadline is not None and now > self.deadline
