"""Shared receive/release behaviour for equipment that holds plates."""

from __future__ import annotations

from typing import Any, ClassVar

from bioflow.core.simulation import SimulationContext
from bioflow.domain import EquipmentKind, Plate, PlateState
from bioflow.equipment.base import S, Equipment
from bioflow.equipment.events import EquipmentEvent
from bioflow.equipment.slots import PlateSlots


class ContainerEquipment(Equipment[S]):
    """Equipment that plates can be placed into and taken out of.

    Subclasses customise behaviour through three small hooks instead of
    re-implementing receive/release:

        * ``placed_plate_state``    – the state a plate enters when received
        * ``_check_can_release``    – veto an unsafe removal
        * ``_after_occupancy_change`` – update equipment state (e.g. FULL)
    """

    placed_plate_state: ClassVar[PlateState]

    def __init__(
        self,
        equipment_id: str,
        kind: EquipmentKind,
        context: SimulationContext,
        initial_state: S,
        capacity: int,
    ) -> None:
        super().__init__(equipment_id, kind, context, initial_state)
        self._slots = PlateSlots(equipment_id, capacity)

    @property
    def capacity(self) -> int:
        return self._slots.capacity

    @property
    def occupancy(self) -> int:
        return self._slots.count

    @property
    def has_space(self) -> bool:
        return not self._slots.is_full

    @property
    def plate_ids(self) -> tuple[str, ...]:
        return self._slots.plate_ids

    def holds(self, plate_id: str) -> bool:
        return plate_id in self._slots

    def receive(self, plate: Plate) -> None:
        """Place ``plate`` inside this equipment."""
        self._slots.add(plate)
        plate.location_id = self.equipment_id
        plate.state = self.placed_plate_state
        self._publish(EquipmentEvent.PLATE_RECEIVED, plate_id=plate.plate_id)
        if self.is_operational:  # a faulted unit keeps its fault state while plates are evacuated
            self._after_occupancy_change()

    def release(self, plate_id: str) -> Plate:
        """Take a plate out. The caller (a robot) becomes responsible for its location."""
        self._slots.get(plate_id)  # raises UnknownEntityError before any safety check
        self._check_can_release(plate_id)
        plate = self._slots.remove(plate_id)
        self._publish(EquipmentEvent.PLATE_RELEASED, plate_id=plate_id)
        if self.is_operational:
            self._after_occupancy_change()
        return plate

    def _leave_system(self, plate_id: str, location: str) -> None:
        """The plate leaves the automated lab for good (archived off-site, or disposed of).

        Its slot is freed at once. Keeping finished plates in their slots would let a long run
        fill storage and deadlock the lab.
        """
        plate = self._slots.remove(plate_id)
        plate.location_id = location
        self._publish(EquipmentEvent.PLATE_RELEASED, plate_id=plate_id)
        if self.is_operational:
            self._after_occupancy_change()

    def _check_can_release(self, plate_id: str) -> None:
        """Raise SafetyViolationError if ``plate_id`` must not be removed now."""

    def _after_occupancy_change(self) -> None:
        """Update equipment state after a plate enters or leaves."""

    def _snapshot_details(self) -> dict[str, Any]:
        return {"capacity": self.capacity, "occupancy": self.occupancy, "plate_ids": list(self.plate_ids)}
