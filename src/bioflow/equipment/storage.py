"""Storage area: where new plates wait and finished plates are archived."""

from __future__ import annotations

from enum import StrEnum

from bioflow.core.simulation import SimulationContext
from bioflow.core.state_machine import TransitionTable
from bioflow.domain import EquipmentKind, PlateState
from bioflow.equipment.config import ContainerSpec
from bioflow.equipment.container import ContainerEquipment
from bioflow.equipment.events import EquipmentEvent


ARCHIVE_LOCATION = "OFFSITE_ARCHIVE"  # where archived plates go when they leave the automated lab


class StorageState(StrEnum):
    AVAILABLE = "AVAILABLE"
    FULL = "FULL"


# Passive containers have no moving parts to fail, so they have no fault states.
STORAGE_TRANSITIONS = TransitionTable.build(
    StorageState,
    {StorageState.AVAILABLE: {StorageState.FULL}, StorageState.FULL: {StorageState.AVAILABLE}},
)


class Storage(ContainerEquipment[StorageState]):
    placed_plate_state = PlateState.STORED
    transitions = STORAGE_TRANSITIONS

    def __init__(self, equipment_id: str, context: SimulationContext, spec: ContainerSpec) -> None:
        super().__init__(
            equipment_id, EquipmentKind.STORAGE, context, StorageState.AVAILABLE, spec.capacity
        )

    def archive(self, plate_id: str) -> None:
        """Finish a stored plate: it is checked out to the off-site archive and its slot is freed."""
        plate = self._slots.get(plate_id)
        plate.state = PlateState.ARCHIVED
        self._publish(EquipmentEvent.PLATE_ARCHIVED, plate_id=plate_id)
        self._leave_system(plate_id, ARCHIVE_LOCATION)

    def _after_occupancy_change(self) -> None:
        self._set_state(StorageState.FULL if self._slots.is_full else StorageState.AVAILABLE)
