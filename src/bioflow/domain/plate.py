"""Cell-culture plates: the physical items that flow through the laboratory."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from bioflow.core.exceptions import SimulationError
from bioflow.domain._validation import require
from bioflow.domain.conditions import CultureConditions


class PlateState(StrEnum):
    CREATED = "CREATED"
    STORED = "STORED"
    IN_TRANSIT = "IN_TRANSIT"
    INCUBATING = "INCUBATING"
    PROCESSING = "PROCESSING"  # at a media-exchange or imaging station
    WAITING = "WAITING"  # parked at equipment, waiting for its next task
    QUARANTINED = "QUARANTINED"
    ARCHIVED = "ARCHIVED"
    DISPOSED = "DISPOSED"


class ContaminationStatus(StrEnum):
    CLEAN = "CLEAN"
    SUSPECTED = "SUSPECTED"
    CONTAMINATED = "CONTAMINATED"


@dataclass(eq=False)
class Plate:
    """A single plate.

    Deliberately *not* stored here, to keep one source of truth:
        * priority / deadline  -> owned by the Experiment
        * next operation       -> derived from the task graph
        * culture age          -> computed from ``created_at`` and current time

    ``eq=False`` gives identity semantics: two Plate objects are the same plate
    only if they are the same object, and plates remain hashable while mutable.
    """

    plate_id: str
    experiment_id: str
    cell_type: str
    created_at: float
    conditions: CultureConditions
    location_id: str | None = None
    state: PlateState = PlateState.CREATED
    contamination: ContaminationStatus = ContaminationStatus.CLEAN
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require(bool(self.plate_id.strip()), "plate_id must not be empty")
        require(bool(self.experiment_id.strip()), f"{self.plate_id}: experiment_id must not be empty")
        require(self.created_at >= 0, f"{self.plate_id}: created_at must be >= 0")

    def culture_age_min(self, now: float) -> float:
        """Minutes since the plate was created."""
        if now < self.created_at:
            raise SimulationError(
                f"{self.plate_id}: time {now} is before creation time {self.created_at}"
            )
        return now - self.created_at

    @property
    def is_finished(self) -> bool:
        return self.state in (PlateState.ARCHIVED, PlateState.DISPOSED)
