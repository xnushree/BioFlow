"""Incubators: multi-plate equipment that holds each plate for a timed incubation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from bioflow.core.events import Event
from bioflow.core.exceptions import SafetyViolationError
from bioflow.core.simulation import SimulationContext
from bioflow.core.state_machine import TransitionTable
from bioflow.core.validation import require
from bioflow.domain import EquipmentKind, Operation, PlateState
from bioflow.equipment.config import IncubatorSpec
from bioflow.equipment.container import ContainerEquipment
from bioflow.equipment.events import EquipmentEvent

_INCUBATION_TIMER = "INCUBATION_TIMER"


class IncubatorState(StrEnum):
    AVAILABLE = "AVAILABLE"  # at least one free slot
    FULL = "FULL"
    ENVIRONMENTAL_FAULT = "ENVIRONMENTAL_FAULT"  # temperature/CO2 out of tolerance
    FAULT = "FAULT"  # hardware failure
    RECOVERY = "RECOVERY"


INCUBATOR_TRANSITIONS = TransitionTable.build(
    IncubatorState,
    {
        IncubatorState.AVAILABLE: {IncubatorState.FULL, IncubatorState.ENVIRONMENTAL_FAULT},
        IncubatorState.FULL: {IncubatorState.AVAILABLE, IncubatorState.ENVIRONMENTAL_FAULT},
        IncubatorState.ENVIRONMENTAL_FAULT: {IncubatorState.RECOVERY},
        IncubatorState.FAULT: {IncubatorState.RECOVERY},
        IncubatorState.RECOVERY: {IncubatorState.AVAILABLE, IncubatorState.FULL},
    },
    from_any={IncubatorState.FAULT},
)


class Incubator(ContainerEquipment[IncubatorState]):
    """Holds up to ``capacity`` plates, each incubating on its own timer.

    The environment readings (``temperature_c``, ``co2_pct``) start at the
    setpoint. They are the *observable* values the fault detector will read in
    Phase 16; fault injection in Phase 15 will perturb them.
    """

    placed_plate_state = PlateState.WAITING
    transitions = INCUBATOR_TRANSITIONS

    def __init__(self, equipment_id: str, context: SimulationContext, spec: IncubatorSpec) -> None:
        super().__init__(
            equipment_id, EquipmentKind.INCUBATOR, context, IncubatorState.AVAILABLE, spec.capacity
        )
        self.setpoint = spec.setpoint
        self.temperature_c = spec.temperature_c
        self.co2_pct = spec.co2_pct
        self._timers: dict[str, str] = {}  # plate_id -> scheduled completion event_id

    def is_incubating(self, plate_id: str) -> bool:
        return plate_id in self._timers

    def start_incubation(self, plate_id: str, duration_min: float) -> None:
        """Begin incubating a plate that is already inside this incubator."""
        plate = self._slots.get(plate_id)
        require(duration_min > 0, f"{self.equipment_id}: duration_min must be positive")
        if self.is_incubating(plate_id):
            raise SafetyViolationError(self.equipment_id, f"{plate_id} is already incubating")
        plate.state = PlateState.INCUBATING
        timer = self._context.schedule(
            duration_min, _INCUBATION_TIMER, self.equipment_id, self._finish_incubation,
            payload={"plate_id": plate_id},
        )
        self._timers[plate_id] = timer.event_id
        self._publish(
            EquipmentEvent.PROCESSING_STARTED,
            plate_id=plate_id, operation=Operation.INCUBATE, duration_min=duration_min,
        )

    def _finish_incubation(self, event: Event) -> None:
        plate_id = event.payload["plate_id"]
        del self._timers[plate_id]
        self._slots.get(plate_id).state = PlateState.WAITING
        self._publish(EquipmentEvent.PROCESSING_COMPLETED, plate_id=plate_id, operation=Operation.INCUBATE)

    def _check_can_release(self, plate_id: str) -> None:
        if self.is_incubating(plate_id):
            raise SafetyViolationError(self.equipment_id, f"cannot remove {plate_id} while it is incubating")

    def _after_occupancy_change(self) -> None:
        self._set_state(IncubatorState.FULL if self._slots.is_full else IncubatorState.AVAILABLE)

    def _snapshot_details(self) -> dict[str, Any]:
        return {
            **super()._snapshot_details(),
            "incubating": sorted(self._timers),
            "temperature_c": self.temperature_c,
            "co2_pct": self.co2_pct,
        }
