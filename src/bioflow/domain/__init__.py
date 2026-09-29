"""Domain model: the plain data objects the rest of BioFlow-X reasons about.

Units convention (used everywhere in the project):
    * Simulation time and durations are floats in **minutes**.
    * Absolute times (``created_at``, ``deadline``) are minutes since simulation start.
    * Field names carry their unit where it is not obvious (``duration_min``, ``temperature_c``).

Domain objects enforce their own invariants (e.g. a duration cannot be negative)
but contain no timing, scheduling, or I/O logic.
"""

from bioflow.domain.conditions import CultureConditions
from bioflow.domain.equipment import EQUIPMENT_FOR_OPERATION, EquipmentKind
from bioflow.domain.experiment import Experiment, ExperimentStatus
from bioflow.domain.operation import Operation
from bioflow.domain.plate import ContaminationStatus, Plate, PlateState
from bioflow.domain.protocol import Protocol, ProtocolStep
from bioflow.domain.task import Task, TaskStatus

__all__ = [
    "EQUIPMENT_FOR_OPERATION",
    "ContaminationStatus",
    "CultureConditions",
    "EquipmentKind",
    "Experiment",
    "ExperimentStatus",
    "Operation",
    "Plate",
    "PlateState",
    "Protocol",
    "ProtocolStep",
    "Task",
    "TaskStatus",
]
