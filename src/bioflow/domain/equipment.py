"""Equipment categories and which category performs which operation.

This module holds only the *classification* of equipment. Behaviour (timing,
state machines, capacity) lives in ``bioflow.equipment`` from Phase 5 onward.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType

from bioflow.domain.operation import Operation


class EquipmentKind(StrEnum):
    ROBOT = "ROBOT"
    INCUBATOR = "INCUBATOR"
    MEDIA_STATION = "MEDIA_STATION"
    IMAGING_STATION = "IMAGING_STATION"
    STORAGE = "STORAGE"
    WASTE_STATION = "WASTE_STATION"


# Read-only so no module can quietly remap an operation at runtime.
EQUIPMENT_FOR_OPERATION: MappingProxyType[Operation, EquipmentKind] = MappingProxyType(
    {
        Operation.INCUBATE: EquipmentKind.INCUBATOR,
        Operation.MEDIA_EXCHANGE: EquipmentKind.MEDIA_STATION,
        Operation.IMAGE: EquipmentKind.IMAGING_STATION,
        Operation.ARCHIVE: EquipmentKind.STORAGE,
        Operation.DISPOSE: EquipmentKind.WASTE_STATION,
        Operation.TRANSPORT: EquipmentKind.ROBOT,
    }
)
