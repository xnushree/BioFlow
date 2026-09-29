"""Incubators: multi-plate equipment that holds each plate for a timed incubation."""

from __future__ import annotations

import math
import random
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

# Environment model (abstract workflow constraints, not a biological model).
AMBIENT_TEMPERATURE_C = 22.0
AMBIENT_CO2_PCT = 0.04
HEAT_LOSS_TIME_CONSTANT_MIN = 60.0  # after a heater/gas failure, 63 % of the gap to ambient is lost in an hour
SENSOR_NOISE_TEMPERATURE_C = 0.05
SENSOR_NOISE_CO2_PCT = 0.02
SENSOR_MODES = ("stuck", "dropout")


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

    Two views of the environment are kept apart on purpose:
        * ``true_environment(now)``: the physical truth, changed only by fault injection;
        * ``read_sensor(rng)``: what the sensor reports (truth + noise, or garbage if the
          sensor has failed). Only readings are visible to monitoring and the fault detector.
    """

    placed_plate_state = PlateState.WAITING
    transitions = INCUBATOR_TRANSITIONS
    non_operational_states = frozenset(
        {IncubatorState.ENVIRONMENTAL_FAULT, IncubatorState.FAULT, IncubatorState.RECOVERY}
    )

    def __init__(self, equipment_id: str, context: SimulationContext, spec: IncubatorSpec) -> None:
        super().__init__(
            equipment_id, EquipmentKind.INCUBATOR, context, IncubatorState.AVAILABLE, spec.capacity
        )
        self.setpoint = spec.setpoint
        self._timers: dict[str, str] = {}  # plate_id -> scheduled completion event_id
        # Hidden hardware condition (set by fault injection).
        self._temperature_offset_c = 0.0
        self._co2_offset_pct = 0.0
        self._climate_failed_at: float | None = None
        self._sensor_mode: str | None = None
        self.last_reading: tuple[float, float] | None = None

    # ------------------------------------------------------------ incubation
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

    # ------------------------------------------------------------ environment
    def true_environment(self, now: float) -> tuple[float, float]:
        """Actual (temperature_c, co2_pct) inside the chamber: physical truth, hidden from control."""
        temperature = self.setpoint.temperature_c + self._temperature_offset_c
        co2 = self.setpoint.co2_pct + self._co2_offset_pct
        if self._climate_failed_at is not None:
            decay = math.exp(-(now - self._climate_failed_at) / HEAT_LOSS_TIME_CONSTANT_MIN)
            temperature = AMBIENT_TEMPERATURE_C + (temperature - AMBIENT_TEMPERATURE_C) * decay
            co2 = AMBIENT_CO2_PCT + (co2 - AMBIENT_CO2_PCT) * decay
        return temperature, co2

    def read_sensor(self, rng: random.Random) -> tuple[float, float]:
        """What the environment sensor reports now."""
        if self._sensor_mode == "dropout":
            reading = (math.nan, math.nan)
        elif self._sensor_mode == "stuck" and self.last_reading is not None:
            reading = self.last_reading
        else:
            temperature, co2 = self.true_environment(self._context.now)
            reading = (temperature + rng.gauss(0.0, SENSOR_NOISE_TEMPERATURE_C),
                       co2 + rng.gauss(0.0, SENSOR_NOISE_CO2_PCT))
        self.last_reading = reading
        return reading

    # -------------------------------------------- hidden hardware (injection)
    def fail_climate_control(self) -> None:
        """Heater and gas supply stop: the chamber drifts towards room conditions."""
        self._climate_failed_at = self._context.now

    def repair_climate_control(self) -> None:
        self._climate_failed_at = None

    def set_environment_offset(self, temperature_c: float = 0.0, co2_pct: float = 0.0) -> None:
        """Hold the chamber away from its setpoint (an excursion). Zero offsets restore it."""
        self._temperature_offset_c = temperature_c
        self._co2_offset_pct = co2_pct

    def set_sensor_mode(self, mode: str | None) -> None:
        """None = healthy sensor; 'stuck' repeats the last value; 'dropout' reports NaN."""
        require(mode is None or mode in SENSOR_MODES, f"unknown sensor mode {mode!r}")
        self._sensor_mode = mode

    # ------------------------------------------------------------------ hooks
    def _check_can_release(self, plate_id: str) -> None:
        if self.is_incubating(plate_id):
            raise SafetyViolationError(self.equipment_id, f"cannot remove {plate_id} while it is incubating")

    def _after_occupancy_change(self) -> None:
        self._set_state(IncubatorState.FULL if self._slots.is_full else IncubatorState.AVAILABLE)

    def _snapshot_details(self) -> dict[str, Any]:
        """Observed values only: the digital twin shows what sensors report, not hidden truth."""
        reading = self.last_reading
        return {
            **super()._snapshot_details(),
            "incubating": sorted(self._timers),
            "temperature_c": reading[0] if reading else None,
            "co2_pct": reading[1] if reading else None,
        }
