"""The scheduling-policy interface.

A policy makes *decisions only*; the Dispatcher carries them out and checks
feasibility (free destination, idle robot). A policy answers three questions:

    1. ``order``              - in what order should ready tasks be tried?
    2. ``choose_destination`` - which of the free, suitable equipment should a task use?
    3. ``choose_robot``       - which idle robot should move the plate?

The defaults for 2 and 3 pick the first candidate (lowest ID). Policies that
only change task order (FIFO, priority, deadline) therefore make identical
resource choices, which keeps benchmark comparisons between them fair.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from bioflow.domain import EquipmentKind, Experiment, Plate, Task
from bioflow.equipment.base import Equipment
from bioflow.robotics.travel import TravelTimeModel


class AvailabilityQuery(Protocol):
    """The read-only part of the ResourceManager a policy may consult."""

    def available(self, kind: EquipmentKind) -> list[str]: ...

    def available_robots(self) -> list[str]: ...


@dataclass(frozen=True)
class SchedulingView:
    """Read-only information a policy may base its decisions on."""

    now: float
    experiments: Mapping[str, Experiment]
    plates: Mapping[str, Plate]
    equipment: Mapping[str, Equipment[Any]]
    resources: AvailabilityQuery
    travel: TravelTimeModel

    def experiment_of(self, task: Task) -> Experiment:
        return self.experiments[task.experiment_id]

    def plate_of(self, task: Task) -> Plate:
        return self.plates[task.plate_id]

    def plate_location(self, task: Task) -> str:
        return self.plates[task.plate_id].location_id or ""

    def location_of(self, equipment_id: str) -> str:
        """Where a piece of equipment is; for a robot, where it currently stands."""
        return getattr(self.equipment[equipment_id], "location_id", equipment_id)


class Scheduler(ABC):
    name: ClassVar[str]

    @abstractmethod
    def order(self, ready: Sequence[Task], view: SchedulingView) -> list[Task]:
        """Return ``ready`` tasks in the order the dispatcher should try them."""

    def choose_destination(self, task: Task, candidates: Sequence[str], view: SchedulingView) -> str:
        """Pick one of ``candidates`` (non-empty, all free and suitable)."""
        return candidates[0]

    def choose_robot(self, task: Task, robots: Sequence[str], view: SchedulingView) -> str:
        """Pick one of ``robots`` (non-empty, all idle)."""
        return robots[0]
