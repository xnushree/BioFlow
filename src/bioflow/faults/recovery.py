"""Automatic recovery from detected faults.

Triggered only by the detector (FAULT_DETECTED / FAULT_CLEARED) and by
maintenance sign-offs, so recovery acts on what the control system knows,
never on hidden ground truth.

Policy per diagnosis:

    environment / sensor (incubator)
        ENVIRONMENTAL_FAULT; interrupt incubations (the remaining time is kept);
        exposed plates -> contamination SUSPECTED; re-queue their tasks so the
        scheduler evacuates them to healthy incubators by its own priorities.
    station failure
        FAULT; abort the hung run; re-queue the task to redo it elsewhere.
    robot failure
        FAULT; not carrying -> abort the job so another robot takes it;
        carrying -> hold (the plate cannot be handed over) and resume after repair.
    slow drive / gripper failure / lost link
        out of service (no new work; its own state machine untouched); gripper ->
        abort the pending pick so another robot tries; back in service on
        maintenance sign-off or when heartbeats return.

If affected work has nowhere to go, recovery enters a *safe hold*
(RECOVERY_BLOCKED, with the reason). If the fault is still unresolved after
``max_hold_min``, it is declared UNRECOVERABLE: the stranded plates are
quarantined and their remaining work is cancelled, with the reason reported.
Recovery completes when the equipment is back in service.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from statistics import mean
from typing import Any

from bioflow.control.dispatcher import Dispatcher
from bioflow.control.resource_manager import ResourceManager
from bioflow.control.state_manager import StateManager
from bioflow.core.event_bus import EventBus
from bioflow.core.events import Event
from bioflow.core.simulation import SimulationContext
from bioflow.domain import ContaminationStatus, EquipmentKind, PlateState
from bioflow.equipment.incubator import Incubator, IncubatorState
from bioflow.equipment.robot import Robot
from bioflow.equipment.station import ProcessingStation
from bioflow.faults.config import RecoverySettings
from bioflow.faults.fault import FaultType
from bioflow.faults.fault_detector import DetectionEvent, FaultDetector

logger = logging.getLogger(__name__)

SOURCE_ID = "RECOVERY"
_ENVIRONMENT = frozenset({FaultType.TEMPERATURE_EXCURSION, FaultType.CO2_EXCURSION, FaultType.INCUBATOR_FAILURE})
_STATION = frozenset({FaultType.MEDIA_STATION_FAILURE, FaultType.IMAGING_FAILURE})
_OUT_OF_SERVICE = frozenset({FaultType.ROBOT_TIMEOUT, FaultType.PLATE_DETECTION_FAILURE,
                             FaultType.COMMUNICATION_TIMEOUT})


class RecoveryEvent(StrEnum):
    RECOVERY_STARTED = "RECOVERY_STARTED"  # payload: recovery_id, fault_type, equipment_id, affected_plates, actions
    RECOVERY_BLOCKED = "RECOVERY_BLOCKED"  # payload: recovery_id, equipment_id, reason (safe hold)
    RECOVERY_COMPLETED = "RECOVERY_COMPLETED"  # payload: recovery_id, equipment_id, duration_min
    UNRECOVERABLE = "UNRECOVERABLE_FAULT"  # payload: recovery_id, equipment_id, reason, plates


@dataclass(eq=False)
class Recovery:
    recovery_id: str
    detection_id: str
    fault_type: FaultType
    equipment_id: str
    started_at: float
    affected_plates: list[str] = field(default_factory=list)
    rescheduled_tasks: int = 0
    actions: list[str] = field(default_factory=list)
    blocked_reason: str | None = None
    completed_at: float | None = None
    unrecoverable: bool = False

    @property
    def duration_min(self) -> float | None:
        return self.completed_at - self.started_at if self.completed_at is not None else None


@dataclass(frozen=True)
class RecoveryReport:
    recoveries: int
    completed: int
    unrecoverable: int
    blocked: int
    tasks_rescheduled: int
    plates_affected: int
    mean_recovery_min: float | None

    def format(self) -> str:
        mean_text = f"{self.mean_recovery_min:.1f} min" if self.mean_recovery_min is not None else "-"
        return (f"Recoveries:         {self.recoveries} started, {self.completed} completed "
                f"(mean {mean_text}), {self.blocked} needed a safe hold, {self.unrecoverable} unrecoverable\n"
                f"Affected work:      {self.plates_affected} plates, {self.tasks_rescheduled} tasks rescheduled")


class RecoveryManager:
    def __init__(
        self,
        context: SimulationContext,
        bus: EventBus,
        state: StateManager,
        resources: ResourceManager,
        dispatcher: Dispatcher,
        detector: FaultDetector,
        settings: RecoverySettings,
    ) -> None:
        self._context = context
        self._state = state
        self._resources = resources
        self._dispatcher = dispatcher
        self._detector = detector
        self.settings = settings
        self._recoveries: list[Recovery] = []
        self._open: dict[str, Recovery] = {}  # detection_id -> recovery in progress
        self._ids = itertools.count(1)
        self._handlers: dict[FaultType, Callable[[Recovery], None]] = {
            **{t: self._recover_environment for t in _ENVIRONMENT},
            FaultType.SENSOR_FAILURE: self._recover_environment,
            **{t: self._recover_station for t in _STATION},
            FaultType.ROBOT_FAILURE: self._recover_robot,
            **{t: self._take_out_of_service for t in _OUT_OF_SERVICE},
        }
        bus.subscribe(DetectionEvent.FAULT_DETECTED, self._on_detected)
        bus.subscribe(DetectionEvent.FAULT_CLEARED, self._on_cleared)

    # ------------------------------------------------------------------ queries
    @property
    def recoveries(self) -> list[Recovery]:
        return list(self._recoveries)

    def report(self) -> RecoveryReport:
        done = [r.duration_min for r in self._recoveries if r.duration_min is not None and not r.unrecoverable]
        return RecoveryReport(
            recoveries=len(self._recoveries),
            completed=len(done),
            unrecoverable=sum(r.unrecoverable for r in self._recoveries),
            blocked=sum(r.blocked_reason is not None for r in self._recoveries),
            tasks_rescheduled=sum(r.rescheduled_tasks for r in self._recoveries),
            plates_affected=len({p for r in self._recoveries for p in r.affected_plates}),
            mean_recovery_min=mean(done) if done else None,
        )

    # ---------------------------------------------------------------- triggers
    def _on_detected(self, event: Event) -> None:
        p = event.payload
        recovery = Recovery(f"REC{next(self._ids):04d}", p["detection_id"], FaultType(p["fault_type"]),
                            p["equipment_id"], self._context.now)
        self._recoveries.append(recovery)
        self._open[recovery.detection_id] = recovery
        self._handlers[recovery.fault_type](recovery)
        self._publish(RecoveryEvent.RECOVERY_STARTED, recovery, fault_type=recovery.fault_type,
                      affected_plates=list(recovery.affected_plates), actions=list(recovery.actions))

    def _on_cleared(self, event: Event) -> None:
        recovery = self._open.get(event.payload["detection_id"])
        if recovery is not None:
            self._restore(recovery)

    # --------------------------------------------------------------- policies
    def _recover_environment(self, recovery: Recovery) -> None:
        incubator = self._equipment(recovery, Incubator)
        incubator.enter_fault(IncubatorState.ENVIRONMENTAL_FAULT)
        recovery.actions.append("incubator taken out of service")
        exposed = recovery.fault_type is not FaultType.SENSOR_FAILURE
        for plate_id in incubator.plate_ids:
            plate = self._state.plate(plate_id)
            if exposed and plate.contamination is ContaminationStatus.CLEAN:
                plate.contamination = ContaminationStatus.SUSPECTED
            recovery.affected_plates.append(plate_id)
            if incubator.is_incubating(plate_id):
                remaining = incubator.interrupt_incubation(plate_id)
                self._requeue(recovery, plate_id, f"evacuating {recovery.equipment_id}", remaining)
        if exposed and recovery.affected_plates:
            recovery.actions.append(f"{len(recovery.affected_plates)} exposed plates marked SUSPECTED")
        if recovery.rescheduled_tasks and not self._alternatives(recovery.equipment_id, EquipmentKind.INCUBATOR):
            self._block(recovery, f"no other incubator in service for {recovery.rescheduled_tasks} evacuated plates")

    def _recover_station(self, recovery: Recovery) -> None:
        station = self._equipment(recovery, ProcessingStation)
        station.enter_fault()
        recovery.actions.append("station taken out of service")
        plate_id = station.abort_processing()
        if plate_id is not None:
            recovery.affected_plates.append(plate_id)
            self._requeue(recovery, plate_id, f"{recovery.equipment_id} failed during {station.operation}")
            if not self._alternatives(recovery.equipment_id, station.kind):
                self._block(recovery, f"no other {station.kind} in service to redo {plate_id}")

    def _recover_robot(self, recovery: Recovery) -> None:
        robot = self._equipment(recovery, Robot)
        robot.enter_fault()
        recovery.actions.append("robot taken out of service")
        if robot.job is None:
            return
        recovery.affected_plates.append(robot.job.plate_id)
        if robot.carrying is None:
            job = robot.abort_job()
            self._requeue(recovery, job.plate_id, f"{robot.equipment_id} failed before pickup")
            recovery.actions.append("job handed to another robot")
        else:
            self._block(recovery, f"{robot.carrying.plate_id} is on board; waiting for repair")

    def _take_out_of_service(self, recovery: Recovery) -> None:
        self._resources.take_out_of_service(recovery.equipment_id)
        recovery.actions.append("taken out of service")
        robot = self._state.equipment_item(recovery.equipment_id)
        if recovery.fault_type is FaultType.PLATE_DETECTION_FAILURE and isinstance(robot, Robot) \
                and robot.job is not None and robot.carrying is None:
            job = robot.abort_job()
            recovery.affected_plates.append(job.plate_id)
            self._requeue(recovery, job.plate_id, f"{robot.equipment_id} cannot detect plates")
            recovery.actions.append("pick handed to another robot")

    # ------------------------------------------------------------ restoration
    def _restore(self, recovery: Recovery) -> None:
        """Symptoms gone: bring the equipment back once no other diagnosis is holding it."""
        del self._open[recovery.detection_id]
        if recovery.unrecoverable:
            return
        eid = recovery.equipment_id
        if any(r.equipment_id == eid for r in self._open.values()):
            return  # e.g. a sensor fault is still open on the same incubator
        equipment = self._state.equipment_item(eid)
        if recovery.fault_type in _OUT_OF_SERVICE:
            self._resources.return_to_service(eid)
            if isinstance(equipment, Robot) and equipment.hardware_ok:
                equipment.resume()  # re-sync a robot whose silence may have been a real stoppage
        elif not equipment.is_operational:
            equipment.begin_recovery()
            equipment.complete_recovery()
        recovery.completed_at = self._context.now
        recovery.actions.append("back in service")
        self._publish(RecoveryEvent.RECOVERY_COMPLETED, recovery, duration_min=recovery.duration_min)
        # Work may be waiting *inside* the recovered equipment (e.g. a plate whose run was aborted),
        # which no "capacity available" announcement would trigger, so re-plan explicitly.
        self._dispatcher.dispatch()

    def _block(self, recovery: Recovery, reason: str) -> None:
        recovery.blocked_reason = reason
        logger.warning("recovery %s on hold: %s", recovery.recovery_id, reason)
        self._publish(RecoveryEvent.RECOVERY_BLOCKED, recovery, reason=reason)
        self._context.schedule(self.settings.max_hold_min, "RECOVERY_HOLD_EXPIRED", SOURCE_ID,
                               lambda event: self._hold_expired(recovery))

    def _hold_expired(self, recovery: Recovery) -> None:
        if recovery.completed_at is not None:
            return
        equipment = self._state.equipment_item(recovery.equipment_id)
        if isinstance(equipment, Robot):
            stranded = [equipment.carrying.plate_id] if equipment.carrying else []
        else:
            stranded = [pid for pid in recovery.affected_plates
                        if self._state.plate(pid).location_id == recovery.equipment_id
                        and not self._state.plate(pid).is_finished]
        if not stranded:
            return  # the affected work found another way in the meantime
        recovery.unrecoverable = True
        recovery.completed_at = self._context.now
        reason = f"{recovery.blocked_reason}; still unresolved after {self.settings.max_hold_min:g} min"
        for plate_id in stranded:
            self._dispatcher.fail_plate(plate_id, reason)
            plate = self._state.plate(plate_id)
            if not plate.is_finished:
                plate.state = PlateState.QUARANTINED
        logger.error("%s unrecoverable: %s", recovery.equipment_id, reason)
        self._publish(RecoveryEvent.UNRECOVERABLE, recovery, reason=reason, plates=stranded)

    # ------------------------------------------------------------------ helpers
    def _alternatives(self, equipment_id: str, kind: EquipmentKind) -> list[str]:
        """Other equipment of ``kind`` that is healthy and in service (busy or not).

        Being busy is ordinary queueing; only the absence of any alternative is a reason to hold.
        """
        return [eid for eid, eq in self._state.equipment.items()
                if eq.kind is kind and eid != equipment_id and eq.is_operational and self._resources.in_service(eid)]

    def _requeue(self, recovery: Recovery, plate_id: str, reason: str, remaining_min: float | None = None) -> None:
        task_id = self._dispatcher.active_task_for(plate_id)
        if task_id is None:
            return
        self._dispatcher.requeue(task_id, reason, remaining_duration_min=remaining_min)
        recovery.rescheduled_tasks += 1

    def _equipment(self, recovery: Recovery, kind: type) -> Any:
        equipment = self._state.equipment_item(recovery.equipment_id)
        assert isinstance(equipment, kind), f"{recovery.fault_type} diagnosed on {recovery.equipment_id}"
        return equipment

    def _publish(self, event_type: RecoveryEvent, recovery: Recovery, **payload: Any) -> None:
        self._context.publish(event_type, SOURCE_ID, target=recovery.equipment_id, payload={
            "recovery_id": recovery.recovery_id, "equipment_id": recovery.equipment_id, **payload,
        })
