"""Build every piece of equipment described by an EquipmentConfig."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from bioflow.core.simulation import SimulationContext
from bioflow.domain import Operation
from bioflow.equipment.base import Equipment
from bioflow.equipment.config import EquipmentConfig, EquipmentGroup
from bioflow.equipment.incubator import Incubator
from bioflow.equipment.robot import Robot
from bioflow.equipment.station import ProcessingStation
from bioflow.equipment.storage import Storage
from bioflow.equipment.waste_station import WasteStation
from bioflow.robotics.motion import MotionController

SpecT = TypeVar("SpecT")


def equipment_id(prefix: str, number: int) -> str:
    """Standard equipment ID, e.g. ``equipment_id("ROBOT", 2) == "ROBOT_02"``."""
    return f"{prefix}_{number:02d}"


def build_equipment(
    config: EquipmentConfig, context: SimulationContext, motion: MotionController
) -> dict[str, Equipment[Any]]:
    """Instantiate all equipment, keyed by ID. Robots start at the first storage area."""
    home = equipment_id("STORAGE", 1)
    equipment: dict[str, Equipment[Any]] = {}

    def add(prefix: str, group: EquipmentGroup[SpecT], make: Callable[[str, SpecT], Equipment[Any]]) -> None:
        for number in range(1, group.count + 1):
            new_id = equipment_id(prefix, number)
            equipment[new_id] = make(new_id, group.spec)

    add("STORAGE", config.storage, lambda i, s: Storage(i, context, s))
    add("INCUBATOR", config.incubators, lambda i, s: Incubator(i, context, s))
    add("MEDIA", config.media_stations,
        lambda i, s: ProcessingStation(i, context, Operation.MEDIA_EXCHANGE, s))
    add("IMAGING", config.imaging_stations,
        lambda i, s: ProcessingStation(i, context, Operation.IMAGE, s))
    add("WASTE", config.waste_stations, lambda i, s: WasteStation(i, context, s))
    add("ROBOT", config.robots, lambda i, s: Robot(i, context, s, home_location_id=home, motion=motion))
    return equipment
