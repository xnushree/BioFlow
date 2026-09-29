"""The fault model: what can go wrong, where, when, how badly, and for how long."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from bioflow.core.exceptions import ValidationError
from bioflow.core.validation import require
from bioflow.domain import EquipmentKind


class FaultType(StrEnum):
    ROBOT_FAILURE = "ROBOT_FAILURE"  # controller down: robot freezes
    ROBOT_TIMEOUT = "ROBOT_TIMEOUT"  # degraded drive: robot moves slowly
    INCUBATOR_FAILURE = "INCUBATOR_FAILURE"  # heater/gas supply fails: chamber drifts to room conditions
    TEMPERATURE_EXCURSION = "TEMPERATURE_EXCURSION"  # chamber held away from its temperature setpoint
    CO2_EXCURSION = "CO2_EXCURSION"  # chamber held away from its CO2 setpoint
    MEDIA_STATION_FAILURE = "MEDIA_STATION_FAILURE"  # processing hangs
    IMAGING_FAILURE = "IMAGING_FAILURE"  # processing hangs
    COMMUNICATION_TIMEOUT = "COMMUNICATION_TIMEOUT"  # heartbeats stop arriving; hardware still works
    SENSOR_FAILURE = "SENSOR_FAILURE"  # environment sensor stuck or dropping out; chamber itself fine
    PLATE_DETECTION_FAILURE = "PLATE_DETECTION_FAILURE"  # gripper sensor: picks report "plate not detected"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


_ALL_KINDS = frozenset(EquipmentKind)
VALID_TARGETS: Mapping[FaultType, frozenset[EquipmentKind]] = MappingProxyType({
    FaultType.ROBOT_FAILURE: frozenset({EquipmentKind.ROBOT}),
    FaultType.ROBOT_TIMEOUT: frozenset({EquipmentKind.ROBOT}),
    FaultType.INCUBATOR_FAILURE: frozenset({EquipmentKind.INCUBATOR}),
    FaultType.TEMPERATURE_EXCURSION: frozenset({EquipmentKind.INCUBATOR}),
    FaultType.CO2_EXCURSION: frozenset({EquipmentKind.INCUBATOR}),
    FaultType.MEDIA_STATION_FAILURE: frozenset({EquipmentKind.MEDIA_STATION}),
    FaultType.IMAGING_FAILURE: frozenset({EquipmentKind.IMAGING_STATION}),
    FaultType.COMMUNICATION_TIMEOUT: _ALL_KINDS,
    FaultType.SENSOR_FAILURE: frozenset({EquipmentKind.INCUBATOR}),
    FaultType.PLATE_DETECTION_FAILURE: frozenset({EquipmentKind.ROBOT}),
})

# Size of the physical effect when a fault spec gives no ``magnitude``.
DEFAULT_MAGNITUDE: Mapping[FaultType, float] = MappingProxyType({
    FaultType.ROBOT_TIMEOUT: 4.0,  # travel takes 4x longer
    FaultType.TEMPERATURE_EXCURSION: 2.0,  # +2 degC
    FaultType.CO2_EXCURSION: 1.5,  # +1.5 % CO2
})


@dataclass(frozen=True)
class FaultSpec:
    """A fault to inject.

    Attributes:
        start_min: simulation time at which the fault physically begins.
        duration_min: time until it is repaired, or None if it is never repaired
            (the only way it ends is recovery working around it).
        magnitude: size of the effect for excursions / slowdowns (see DEFAULT_MAGNITUDE).
        metadata: extra options, e.g. ``{"mode": "dropout"}`` for SENSOR_FAILURE.
    """

    fault_type: FaultType
    equipment_id: str
    start_min: float
    duration_min: float | None = None
    severity: Severity = Severity.MEDIUM
    magnitude: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require(bool(self.equipment_id.strip()), "fault equipment_id must not be empty")
        require(math.isfinite(self.start_min) and self.start_min >= 0, f"start_min must be >= 0, got {self.start_min}")
        require(
            self.duration_min is None or self.duration_min > 0,
            f"duration_min must be positive (or omitted for a permanent fault), got {self.duration_min}",
        )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def recoverable(self) -> bool:
        """True if the hardware will be repaired (the fault has a finite duration)."""
        return self.duration_min is not None

    @property
    def effect_size(self) -> float | None:
        return self.magnitude if self.magnitude is not None else DEFAULT_MAGNITUDE.get(self.fault_type)

    def check_target(self, kind: EquipmentKind) -> None:
        if kind not in VALID_TARGETS[self.fault_type]:
            allowed = ", ".join(sorted(VALID_TARGETS[self.fault_type]))
            raise ValidationError(f"{self.fault_type} cannot target {self.equipment_id} ({kind}); needs {allowed}")


class FaultStatus(StrEnum):
    SCHEDULED = "SCHEDULED"  # not started yet
    ACTIVE = "ACTIVE"  # physically present, not yet detected
    DETECTED = "DETECTED"
    RECOVERING = "RECOVERING"
    RESOLVED = "RESOLVED"
    UNRECOVERABLE = "UNRECOVERABLE"


@dataclass(eq=False)
class Fault:
    """Ground-truth record of one injected fault and what happened to it."""

    fault_id: str
    spec: FaultSpec
    status: FaultStatus = FaultStatus.SCHEDULED
    injected_at: float | None = None
    repaired_at: float | None = None

    @property
    def fault_type(self) -> FaultType:
        return self.spec.fault_type

    @property
    def equipment_id(self) -> str:
        return self.spec.equipment_id

    @property
    def is_physically_present(self) -> bool:
        return self.injected_at is not None and self.repaired_at is None
