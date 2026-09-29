"""Simulated laboratory equipment.

Each class models *behaviour over time*: it schedules its own completion
events on the engine and publishes notifications (``EquipmentEvent``) so the
controller, telemetry and dashboard can react without being wired in.
"""

from bioflow.equipment.base import Equipment, PlateHolder
from bioflow.equipment.events import EquipmentEvent
from bioflow.equipment.incubator import Incubator, IncubatorState
from bioflow.equipment.robot import Robot, RobotState, TransportJob
from bioflow.equipment.station import ProcessingStation, StationState
from bioflow.equipment.storage import Storage, StorageState
from bioflow.equipment.waste_station import WasteStation

__all__ = [
    "Equipment",
    "EquipmentEvent",
    "Incubator",
    "IncubatorState",
    "PlateHolder",
    "ProcessingStation",
    "Robot",
    "RobotState",
    "StationState",
    "Storage",
    "StorageState",
    "TransportJob",
    "WasteStation",
]
