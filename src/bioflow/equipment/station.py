"""Single-plate processing stations: media exchange and imaging.

Both behave identically (hold one plate, process it for a time, hand it back),
so they are one class configured with the operation it performs. Two
near-identical classes would duplicate logic without adding behaviour.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from bioflow.core.events import Event
from bioflow.core.exceptions import SafetyViolationError
from bioflow.core.simulation import SimulationContext
from bioflow.core.state_machine import TransitionTable
from bioflow.core.validation import require
from bioflow.domain import EQUIPMENT_FOR_OPERATION, Operation, PlateState
from bioflow.equipment.config import StationSpec
from bioflow.equipment.container import ContainerEquipment
from bioflow.equipment.events import EquipmentEvent

STATION_OPERATIONS = frozenset({Operation.MEDIA_EXCHANGE, Operation.IMAGE})
STATION_CAPACITY = 1
_PROCESSING_TIMER = "PROCESSING_TIMER"


class StationState(StrEnum):
    IDLE = "IDLE"  # empty
    OCCUPIED = "OCCUPIED"  # holds a plate that is not being processed
    PROCESSING = "PROCESSING"
    FAULT = "FAULT"
    RECOVERY = "RECOVERY"


STATION_TRANSITIONS = TransitionTable.build(
    StationState,
    {
        StationState.IDLE: {StationState.OCCUPIED},
        StationState.OCCUPIED: {StationState.IDLE, StationState.PROCESSING},
        StationState.PROCESSING: {StationState.OCCUPIED},
        StationState.FAULT: {StationState.RECOVERY},
        StationState.RECOVERY: {StationState.IDLE, StationState.OCCUPIED},
    },
    from_any={StationState.FAULT},
)


class ProcessingStation(ContainerEquipment[StationState]):
    """Processes one plate at a time with the configured operation."""

    placed_plate_state = PlateState.WAITING
    transitions = STATION_TRANSITIONS

    def __init__(
        self, equipment_id: str, context: SimulationContext, operation: Operation, spec: StationSpec
    ) -> None:
        require(
            operation in STATION_OPERATIONS,
            f"{equipment_id}: a processing station cannot perform {operation}",
        )
        super().__init__(
            equipment_id, EQUIPMENT_FOR_OPERATION[operation], context, StationState.IDLE, STATION_CAPACITY
        )
        self.operation = operation
        self.spec = spec
        self._processing_plate_id: str | None = None

    @property
    def processing_plate_id(self) -> str | None:
        return self._processing_plate_id

    def start_processing(self, plate_id: str, duration_min: float | None = None) -> None:
        """Process the plate in the station. ``None`` duration uses the configured time."""
        plate = self._slots.get(plate_id)
        if self._processing_plate_id is not None:
            raise SafetyViolationError(self.equipment_id, f"already processing {self._processing_plate_id}")
        duration = self.spec.process_time_min if duration_min is None else duration_min
        require(duration > 0, f"{self.equipment_id}: duration_min must be positive")

        plate.state = PlateState.PROCESSING
        self._processing_plate_id = plate_id
        self._set_state(StationState.PROCESSING)
        self._context.schedule(
            duration, _PROCESSING_TIMER, self.equipment_id, self._finish_processing,
            payload={"plate_id": plate_id},
        )
        self._publish(
            EquipmentEvent.PROCESSING_STARTED,
            plate_id=plate_id, operation=self.operation, duration_min=duration,
        )

    def _finish_processing(self, event: Event) -> None:
        plate_id = event.payload["plate_id"]
        self._processing_plate_id = None
        self._slots.get(plate_id).state = PlateState.WAITING
        self._set_state(StationState.OCCUPIED)
        self._publish(EquipmentEvent.PROCESSING_COMPLETED, plate_id=plate_id, operation=self.operation)

    def _check_can_release(self, plate_id: str) -> None:
        if plate_id == self._processing_plate_id:
            raise SafetyViolationError(self.equipment_id, f"cannot remove {plate_id} during {self.operation}")

    def _after_occupancy_change(self) -> None:
        self._set_state(StationState.OCCUPIED if self.occupancy else StationState.IDLE)

    def _snapshot_details(self) -> dict[str, Any]:
        return {
            **super()._snapshot_details(),
            "operation": self.operation,
            "processing_plate_id": self._processing_plate_id,
        }
