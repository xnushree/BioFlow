"""Transport robots: move a plate from one piece of equipment to another.

A transport is a chain of phases:

    IDLE -> ASSIGNED -> MOVING -> PICKING -> TRANSPORTING -> PLACING -> IDLE
                        (travel)   (pick)    (travel)        (place)

Travel legs are delegated to a MotionController ("take me to X, call me back"),
so the same Robot works with simple timed trips or with cell-by-cell
movement, reservations and deadlock handling on the laboratory map.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from collections.abc import Callable
from bioflow.core.exceptions import ResourceUnavailableError
from bioflow.core.simulation import SimulationContext
from bioflow.core.state_machine import TransitionTable
from bioflow.core.validation import require
from bioflow.domain import EquipmentKind, Plate, PlateState
from bioflow.equipment.base import Equipment, PlateHolder
from bioflow.equipment.config import RobotSpec
from bioflow.equipment.events import EquipmentEvent
from bioflow.robotics.motion import MotionController

_PICK_DONE = "ROBOT_PICK_DONE"
_PLACE_DONE = "ROBOT_PLACE_DONE"
_PICK_RETRY = "ROBOT_PICK_RETRY"


@dataclass
class _Phase:
    """A timed pick/place step that can be suspended by a hardware failure and resumed later."""

    event_type: str
    handler: Callable[[], None]
    due: float
    event_id: str | None = None
    remaining: float | None = None  # set while suspended


class RobotState(StrEnum):
    IDLE = "IDLE"
    ASSIGNED = "ASSIGNED"  # has accepted a job and is about to set off
    MOVING = "MOVING"  # travelling empty to the pickup location
    PICKING = "PICKING"
    TRANSPORTING = "TRANSPORTING"  # travelling with the plate
    PLACING = "PLACING"
    SAFE_STOP = "SAFE_STOP"  # halted mid-motion, holding position
    FAULT = "FAULT"
    RECOVERY = "RECOVERY"


ROBOT_TRANSITIONS = TransitionTable.build(
    RobotState,
    {
        RobotState.IDLE: {RobotState.ASSIGNED},
        RobotState.ASSIGNED: {RobotState.MOVING, RobotState.IDLE},  # IDLE: job withdrawn
        RobotState.MOVING: {RobotState.PICKING, RobotState.SAFE_STOP},
        RobotState.PICKING: {RobotState.TRANSPORTING},
        RobotState.TRANSPORTING: {RobotState.PLACING, RobotState.SAFE_STOP},
        RobotState.PLACING: {RobotState.IDLE},
        RobotState.SAFE_STOP: {RobotState.RECOVERY},
        RobotState.FAULT: {RobotState.RECOVERY},
        RobotState.RECOVERY: {RobotState.IDLE},
    },
    from_any={RobotState.FAULT},
)


@dataclass(frozen=True)
class TransportJob:
    """Move ``plate_id`` from ``source`` to ``destination``."""

    plate_id: str
    source: PlateHolder
    destination: PlateHolder

    def __post_init__(self) -> None:
        require(
            self.source.equipment_id != self.destination.equipment_id,
            f"{self.plate_id}: source and destination are both {self.source.equipment_id}",
        )


class Robot(Equipment[RobotState]):
    """An exclusive transport resource that carries one plate at a time."""

    transitions = ROBOT_TRANSITIONS
    non_operational_states = frozenset({RobotState.SAFE_STOP, RobotState.FAULT, RobotState.RECOVERY})

    def __init__(
        self,
        equipment_id: str,
        context: SimulationContext,
        spec: RobotSpec,
        home_location_id: str,
        motion: MotionController,
    ) -> None:
        super().__init__(equipment_id, EquipmentKind.ROBOT, context, RobotState.IDLE)
        self.spec = spec
        self.location_id = home_location_id  # equipment the robot last docked at
        self._motion = motion
        self._job: TransportJob | None = None
        self._carrying: Plate | None = None
        self._phase: _Phase | None = None
        self.pick_attempts = 0
        # Hidden hardware condition, changed only by fault injection.
        self.hardware_ok = True  # False: controller down, robot frozen in place
        self.gripper_sensor_ok = True  # False: picks fail with "plate not detected"
        motion.register(equipment_id)

    @property
    def emits_heartbeat(self) -> bool:
        return self.comms_ok and self.hardware_ok

    @property
    def is_idle(self) -> bool:
        """Free to accept a job. IDLE excludes every fault state, so no separate health check is needed."""
        return self.state is RobotState.IDLE

    @property
    def carrying(self) -> Plate | None:
        return self._carrying

    @property
    def job(self) -> TransportJob | None:
        return self._job

    def start_transport(self, job: TransportJob) -> None:
        """Accept a transport job. The robot must be idle."""
        if not self.is_idle:
            raise ResourceUnavailableError(self.equipment_id, f"busy ({self.state}), cannot accept {job.plate_id}")
        self._job = job
        self._set_state(RobotState.ASSIGNED)
        self._publish(EquipmentEvent.TRANSPORT_STARTED, **self._job_payload(job))
        self._set_state(RobotState.MOVING)
        self._motion.travel(self.equipment_id, self.location_id, job.source.equipment_id, self._arrive_at_source)

    # Each handler below finishes one phase and starts the next.
    def _arrive_at_source(self) -> None:
        job = self._current_job()
        self.location_id = job.source.equipment_id
        self._set_state(RobotState.PICKING)
        self.pick_attempts = 0
        self._schedule_phase(self.spec.pick_time_min, _PICK_DONE, self._finish_pick)

    def _finish_pick(self) -> None:
        job = self._current_job()
        self.pick_attempts += 1
        if not self.gripper_sensor_ok:
            # An observable error code, like a real robot controller would report.
            self._publish(EquipmentEvent.PICK_FAILED, plate_id=job.plate_id, source=job.source.equipment_id,
                          attempt=self.pick_attempts)
            self._schedule_phase(self.spec.pick_time_min, _PICK_RETRY, self._finish_pick)
            return
        plate = job.source.release(job.plate_id)
        plate.location_id = self.equipment_id
        plate.state = PlateState.IN_TRANSIT
        self._carrying = plate
        self._publish(EquipmentEvent.PLATE_PICKED, plate_id=plate.plate_id, source=job.source.equipment_id)
        self._set_state(RobotState.TRANSPORTING)
        self._motion.travel(
            self.equipment_id, job.source.equipment_id, job.destination.equipment_id, self._arrive_at_destination
        )

    def _arrive_at_destination(self) -> None:
        self.location_id = self._current_job().destination.equipment_id
        self._set_state(RobotState.PLACING)
        self._schedule_phase(self.spec.place_time_min, _PLACE_DONE, self._finish_place)

    def _finish_place(self) -> None:
        job = self._current_job()
        plate = self._carrying
        assert plate is not None  # guaranteed by _finish_pick
        job.destination.receive(plate)
        self._carrying = None
        self._job = None
        self._publish(EquipmentEvent.PLATE_PLACED, plate_id=plate.plate_id, destination=job.destination.equipment_id)
        # Become IDLE *before* announcing completion so a listener can assign the next job immediately.
        self._set_state(RobotState.IDLE)
        self._publish(EquipmentEvent.TRANSPORT_COMPLETED, **self._job_payload(job))
        if self.is_idle:  # a listener may already have assigned the next job
            self._motion.robot_idle(self.equipment_id)

    def _current_job(self) -> TransportJob:
        assert self._job is not None, f"{self.equipment_id}: phase event fired without a job"
        return self._job

    def _schedule_phase(self, delay: float, event_type: str, handler: Callable[[], None]) -> None:
        phase = _Phase(event_type, handler, due=self._context.now + delay)
        self._phase = phase
        phase.event_id = self._context.schedule(
            delay, event_type, self.equipment_id, lambda event: self._run_phase(phase),
            payload={"plate_id": self._current_job().plate_id},
        ).event_id

    def _run_phase(self, phase: _Phase) -> None:
        self._phase = None
        phase.handler()

    # -------------------------------------------- hidden hardware (injection)
    def fail_hardware(self) -> None:
        """Controller failure: the robot freezes where it is, mid-motion or mid-pick/place."""
        self.hardware_ok = False
        if self._phase is not None and self._phase.event_id is not None:
            self._context.cancel(self._phase.event_id)
            self._phase.remaining = self._phase.due - self._context.now
            self._phase.event_id = None
        self._motion.freeze(self.equipment_id)

    def repair_hardware(self) -> None:
        """Hardware works again, but the robot stays frozen until recovery resumes it."""
        self.hardware_ok = True

    def resume(self) -> None:
        """Continue exactly where the robot stopped (after repair)."""
        require(self.hardware_ok, f"{self.equipment_id}: cannot resume while hardware is failed")
        self._motion.resume(self.equipment_id)
        phase = self._phase
        if phase is not None and phase.remaining is not None:
            self._phase = None
            self._schedule_phase(phase.remaining, phase.event_type, phase.handler)

    @staticmethod
    def _job_payload(job: TransportJob) -> dict[str, str]:
        return {
            "plate_id": job.plate_id,
            "source": job.source.equipment_id,
            "destination": job.destination.equipment_id,
        }

    def _snapshot_details(self) -> dict[str, Any]:
        return {
            "location_id": self.location_id,
            "carrying": self._carrying.plate_id if self._carrying else None,
            "job": self._job_payload(self._job) if self._job else None,
        }
