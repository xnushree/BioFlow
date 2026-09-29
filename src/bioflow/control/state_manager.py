"""The digital twin's registry: one place that holds every live entity.

Each entity still owns its own state (``plate.state``, ``robot.state``); this
class owns *which entities exist* and how to find them. The API and dashboard
will read the laboratory through it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from bioflow.core.exceptions import UnknownEntityError, ValidationError
from bioflow.domain import Experiment, Plate
from bioflow.equipment.base import Equipment
from bioflow.scheduling.task_graph import TaskGraph


class StateManager:
    def __init__(self, equipment: Mapping[str, Equipment[Any]]) -> None:
        self._equipment = dict(equipment)
        self._experiments: dict[str, Experiment] = {}
        self._plates: dict[str, Plate] = {}
        self.tasks = TaskGraph()

    # Read-only views: callers can look but not add or remove entities.
    @property
    def equipment(self) -> Mapping[str, Equipment[Any]]:
        return MappingProxyType(self._equipment)

    @property
    def experiments(self) -> Mapping[str, Experiment]:
        return MappingProxyType(self._experiments)

    @property
    def plates(self) -> Mapping[str, Plate]:
        return MappingProxyType(self._plates)

    def equipment_item(self, equipment_id: str) -> Equipment[Any]:
        return self._lookup(self._equipment, equipment_id, "equipment")

    def experiment(self, experiment_id: str) -> Experiment:
        return self._lookup(self._experiments, experiment_id, "experiment")

    def plate(self, plate_id: str) -> Plate:
        return self._lookup(self._plates, plate_id, "plate")

    def register_experiment(self, experiment: Experiment, plates: Iterable[Plate]) -> None:
        """Add an experiment and its plates. All-or-nothing on duplicate IDs."""
        plate_list = list(plates)
        if experiment.experiment_id in self._experiments:
            raise ValidationError(f"Experiment {experiment.experiment_id} already exists")
        clashes = sorted(p.plate_id for p in plate_list if p.plate_id in self._plates)
        if clashes:
            raise ValidationError(f"Plates already exist: {clashes}")
        self._experiments[experiment.experiment_id] = experiment
        for plate in plate_list:
            self._plates[plate.plate_id] = plate

    @staticmethod
    def _lookup(table: Mapping[str, Any], key: str, kind: str) -> Any:
        try:
            return table[key]
        except KeyError:
            raise UnknownEntityError(key, kind) from None
