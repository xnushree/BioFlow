"""Waste station: plates placed here are disposed of and never leave."""

from __future__ import annotations

from bioflow.core.exceptions import SafetyViolationError
from bioflow.core.simulation import SimulationContext
from bioflow.domain import EquipmentKind, Plate, PlateState
from bioflow.equipment.config import ContainerSpec
from bioflow.equipment.container import ContainerEquipment
from bioflow.equipment.storage import STORAGE_TRANSITIONS, StorageState


DISPOSED_LOCATION = "DISPOSED"


class WasteStation(ContainerEquipment[StorageState]):
    """Plates placed here are disposed of and leave the lab; capacity is the drop-off buffer.

    Uses the same AVAILABLE/FULL states as storage.
    """

    placed_plate_state = PlateState.DISPOSED
    transitions = STORAGE_TRANSITIONS

    def __init__(self, equipment_id: str, context: SimulationContext, spec: ContainerSpec) -> None:
        super().__init__(
            equipment_id, EquipmentKind.WASTE_STATION, context, StorageState.AVAILABLE, spec.capacity
        )

    def receive(self, plate: Plate) -> None:
        super().receive(plate)
        self._leave_system(plate.plate_id, DISPOSED_LOCATION)

    def _check_can_release(self, plate_id: str) -> None:
        raise SafetyViolationError(self.equipment_id, f"{plate_id} has been disposed and cannot be removed")

    def _after_occupancy_change(self) -> None:
        self._set_state(StorageState.FULL if self._slots.is_full else StorageState.AVAILABLE)
