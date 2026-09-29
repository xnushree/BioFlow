"""Storage area: where new plates wait and finished plates are archived."""

from __future__ import annotations

from enum import StrEnum

from bioflow.core.exceptions import SafetyViolationError
from bioflow.core.simulation import SimulationContext
from bioflow.domain import EquipmentKind, PlateState
from bioflow.equipment.config import ContainerSpec
from bioflow.equipment.container import ContainerEquipment
from bioflow.equipment.events import EquipmentEvent


class StorageState(StrEnum):
    AVAILABLE = "AVAILABLE"
    FULL = "FULL"


class Storage(ContainerEquipment[StorageState]):
    placed_plate_state = PlateState.STORED

    def __init__(self, equipment_id: str, context: SimulationContext, spec: ContainerSpec) -> None:
        super().__init__(
            equipment_id, EquipmentKind.STORAGE, context, StorageState.AVAILABLE, spec.capacity
        )

    def archive(self, plate_id: str) -> None:
        """Mark a stored plate as finished. It stays here, occupying its slot."""
        plate = self._slots.get(plate_id)
        plate.state = PlateState.ARCHIVED
        self._publish(EquipmentEvent.PLATE_ARCHIVED, plate_id=plate_id)

    def _check_can_release(self, plate_id: str) -> None:
        if self._slots.get(plate_id).state is PlateState.ARCHIVED:
            raise SafetyViolationError(self.equipment_id, f"{plate_id} is archived and cannot be removed")

    def _after_occupancy_change(self) -> None:
        self._set_state(StorageState.FULL if self._slots.is_full else StorageState.AVAILABLE)
