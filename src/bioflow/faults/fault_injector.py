"""Fault injection: change the physical truth of the simulated lab at scheduled times.

What the injector publishes:

* ``GroundTruthEvent``: exactly what was injected and repaired. This is for
  scoring detection accuracy and latency afterwards. The fault detector must
  never subscribe to it (a test enforces this).
* ``MaintenanceEvent.MAINTENANCE_COMPLETED``: a technician signs off a repair
  on a piece of equipment. This is observable in a real lab, so recovery may use it.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

from bioflow.core.exceptions import UnknownEntityError, ValidationError
from bioflow.core.simulation import SimulationContext
from bioflow.equipment.base import Equipment
from bioflow.equipment.incubator import Incubator
from bioflow.equipment.robot import Robot
from bioflow.equipment.station import ProcessingStation
from bioflow.faults.fault import Fault, FaultSpec, FaultStatus, FaultType
from bioflow.robotics.motion import MotionController

logger = logging.getLogger(__name__)

SOURCE_ID = "FAULT_INJECTOR"


class GroundTruthEvent(StrEnum):
    FAULT_INJECTED = "GROUND_TRUTH_FAULT_INJECTED"  # payload: fault_id, fault_type, equipment_id
    FAULT_REPAIRED = "GROUND_TRUTH_FAULT_REPAIRED"  # payload: fault_id, fault_type, equipment_id


class MaintenanceEvent(StrEnum):
    MAINTENANCE_COMPLETED = "MAINTENANCE_COMPLETED"  # payload: equipment_id (no fault details)


Effect = Callable[[Equipment[Any], FaultSpec], None]


class FaultInjector:
    def __init__(
        self, context: SimulationContext, equipment: Mapping[str, Equipment[Any]], motion: MotionController
    ) -> None:
        self._context = context
        self._equipment = equipment
        self._motion = motion
        self._faults: list[Fault] = []
        self._ids = itertools.count(1)
        self._effects: dict[FaultType, tuple[Effect, Effect]] = {
            FaultType.ROBOT_FAILURE: (lambda e, s: _robot(e).fail_hardware(),
                                      lambda e, s: _robot(e).repair_hardware()),
            FaultType.ROBOT_TIMEOUT: (lambda e, s: motion.set_speed_factor(e.equipment_id, s.effect_size or 1.0),
                                      lambda e, s: motion.set_speed_factor(e.equipment_id, 1.0)),
            FaultType.PLATE_DETECTION_FAILURE: (lambda e, s: setattr(_robot(e), "gripper_sensor_ok", False),
                                                lambda e, s: setattr(_robot(e), "gripper_sensor_ok", True)),
            FaultType.INCUBATOR_FAILURE: (lambda e, s: _incubator(e).fail_climate_control(),
                                          lambda e, s: _incubator(e).repair_climate_control()),
            FaultType.TEMPERATURE_EXCURSION: (
                lambda e, s: _incubator(e).set_environment_offset(temperature_c=s.effect_size or 0.0),
                lambda e, s: _incubator(e).set_environment_offset()),
            FaultType.CO2_EXCURSION: (
                lambda e, s: _incubator(e).set_environment_offset(co2_pct=s.effect_size or 0.0),
                lambda e, s: _incubator(e).set_environment_offset()),
            FaultType.SENSOR_FAILURE: (lambda e, s: _incubator(e).set_sensor_mode(s.metadata.get("mode", "stuck")),
                                       lambda e, s: _incubator(e).set_sensor_mode(None)),
            FaultType.MEDIA_STATION_FAILURE: (lambda e, s: _station(e).fail_hardware(),
                                              lambda e, s: _station(e).repair_hardware()),
            FaultType.IMAGING_FAILURE: (lambda e, s: _station(e).fail_hardware(),
                                        lambda e, s: _station(e).repair_hardware()),
            FaultType.COMMUNICATION_TIMEOUT: (lambda e, s: setattr(e, "comms_ok", False),
                                              lambda e, s: setattr(e, "comms_ok", True)),
        }

    @property
    def faults(self) -> list[Fault]:
        return list(self._faults)

    def schedule(self, spec: FaultSpec) -> Fault:
        """Plan ``spec``: the fault starts at ``spec.start_min`` and is repaired after its duration.

        Raises:
            UnknownEntityError: the target equipment does not exist.
            ValidationError: wrong kind of target, start in the past, or overlapping
                with another fault of the same type on the same equipment.
        """
        target = self._equipment.get(spec.equipment_id)
        if target is None:
            raise UnknownEntityError(spec.equipment_id, "equipment")
        spec.check_target(target.kind)
        if spec.start_min < self._context.now:
            raise ValidationError(f"fault on {spec.equipment_id} starts at {spec.start_min}, before now")
        for other in self._faults:
            if other.spec.fault_type is spec.fault_type and other.equipment_id == spec.equipment_id \
                    and _overlaps(other.spec, spec):
                raise ValidationError(
                    f"{spec.fault_type} on {spec.equipment_id} overlaps fault {other.fault_id}"
                )

        fault = Fault(f"FLT{next(self._ids):04d}", spec)
        self._faults.append(fault)
        self._context.schedule(spec.start_min - self._context.now, "FAULT_ACTIVATION", SOURCE_ID,
                               lambda event: self._activate(fault))
        return fault

    def _activate(self, fault: Fault) -> None:
        apply, _ = self._effects[fault.fault_type]
        apply(self._equipment[fault.equipment_id], fault.spec)
        fault.status = FaultStatus.ACTIVE
        fault.injected_at = self._context.now
        logger.info("injected %s on %s", fault.fault_type, fault.equipment_id)
        self._ground_truth(GroundTruthEvent.FAULT_INJECTED, fault)
        if fault.spec.duration_min is not None:
            self._context.schedule(fault.spec.duration_min, "FAULT_REPAIR", SOURCE_ID,
                                   lambda event: self._repair(fault))

    def _repair(self, fault: Fault) -> None:
        _, undo = self._effects[fault.fault_type]
        undo(self._equipment[fault.equipment_id], fault.spec)
        fault.repaired_at = self._context.now
        self._ground_truth(GroundTruthEvent.FAULT_REPAIRED, fault)
        self._context.publish(MaintenanceEvent.MAINTENANCE_COMPLETED, fault.equipment_id,
                              payload={"equipment_id": fault.equipment_id})

    def _ground_truth(self, event_type: GroundTruthEvent, fault: Fault) -> None:
        self._context.publish(event_type, SOURCE_ID, target=fault.equipment_id, payload={
            "fault_id": fault.fault_id, "fault_type": fault.fault_type, "equipment_id": fault.equipment_id,
        })


def _overlaps(a: FaultSpec, b: FaultSpec) -> bool:
    a_end = a.start_min + a.duration_min if a.duration_min is not None else float("inf")
    b_end = b.start_min + b.duration_min if b.duration_min is not None else float("inf")
    return a.start_min < b_end and b.start_min < a_end


def _robot(equipment: Equipment[Any]) -> Robot:
    assert isinstance(equipment, Robot)
    return equipment


def _incubator(equipment: Equipment[Any]) -> Incubator:
    assert isinstance(equipment, Incubator)
    return equipment


def _station(equipment: Equipment[Any]) -> ProcessingStation:
    assert isinstance(equipment, ProcessingStation)
    return equipment
