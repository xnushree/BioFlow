"""The supervisory executor: turns scheduling decisions into equipment commands.

Life of a task:

    READY --dispatch--> reserve destination, mark RUNNING, robot moves the plate
          --PLATE_PLACED at destination--> start incubation / processing / archive
          --PROCESSING_COMPLETED--> mark COMPLETED -> dependents become READY -> dispatch again

The Dispatcher reacts only to bus events, so equipment and resources never
call it directly. The Scheduler policy decides *which* task, destination and
robot; the Dispatcher checks that the choice is feasible and executes it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from bioflow.control.resource_manager import ResourceEvent, ResourceManager
from bioflow.control.state_manager import StateManager
from bioflow.core.event_bus import EventBus
from bioflow.core.events import Event
from bioflow.core.exceptions import CapacityExceededError
from bioflow.core.simulation import SimulationContext
from bioflow.domain import (
    EQUIPMENT_FOR_OPERATION,
    EquipmentKind,
    Experiment,
    ExperimentStatus,
    Operation,
    Task,
    TaskStatus,
)
from bioflow.equipment import EquipmentEvent, Incubator, ProcessingStation, Robot, Storage, TransportJob
from bioflow.protocols.task_builder import build_tasks
from bioflow.robotics.travel import TravelTimeModel
from bioflow.scheduling.base_scheduler import Scheduler, SchedulingView

logger = logging.getLogger(__name__)

SOURCE_ID = "DISPATCHER"
MIN_REQUEUED_DURATION_MIN = 0.1  # an interrupted incubation still needs a (tiny) positive duration


class DispatchEvent(StrEnum):
    EXPERIMENT_SUBMITTED = "EXPERIMENT_SUBMITTED"  # payload: experiment_id, plates
    EXPERIMENT_FINISHED = "EXPERIMENT_FINISHED"  # payload: experiment_id, status
    TASK_DISPATCHED = "TASK_DISPATCHED"  # payload: task_id, plate_id, operation, destination, robot
    TASK_COMPLETED = "TASK_COMPLETED"  # payload: task_id, plate_id, operation, equipment
    TASK_REQUEUED = "TASK_REQUEUED"  # payload: task_id, plate_id, operation, reason


@dataclass(frozen=True)
class _ActiveTask:
    task_id: str
    destination_id: str


class Dispatcher:
    def __init__(
        self,
        state: StateManager,
        resources: ResourceManager,
        scheduler: Scheduler,
        travel: TravelTimeModel,
        context: SimulationContext,
        bus: EventBus,
    ) -> None:
        self._state = state
        self._resources = resources
        self._scheduler = scheduler
        self._travel = travel
        self._context = context
        self._active: dict[str, _ActiveTask] = {}  # plate_id -> its running task
        self._dispatching = False
        self._dispatch_again = False

        bus.subscribe(EquipmentEvent.PLATE_PLACED, self._on_plate_placed)
        bus.subscribe(EquipmentEvent.PROCESSING_COMPLETED, self._on_processing_completed)
        bus.subscribe(ResourceEvent.AVAILABLE, lambda event: self.dispatch())

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler

    @property
    def travel(self) -> TravelTimeModel:
        return self._travel

    # --------------------------------------------------------------- submission
    def submit(self, experiment: Experiment) -> None:
        """Create the experiment's plates in storage, add its tasks, and start dispatching.

        Raises:
            CapacityExceededError: storage cannot hold the new plates (nothing is registered).
        """
        plates = experiment.create_plates()
        storages = self._resources.available(EquipmentKind.STORAGE)
        free = sum(self._resources.status(s).free for s in storages)
        if free < len(plates):
            raise CapacityExceededError("STORAGE", free)

        tasks = build_tasks(experiment)
        self._state.register_experiment(experiment, plates)
        self._state.tasks.add_tasks(tasks, now=self._context.now)
        for plate in plates:
            storage = self._state.equipment_item(self._resources.available(EquipmentKind.STORAGE)[0])
            storage.receive(plate)  # type: ignore[attr-defined]
        self._publish(DispatchEvent.EXPERIMENT_SUBMITTED, experiment_id=experiment.experiment_id,
                      plates=len(plates))
        self.dispatch()

    # --------------------------------------------------------------- dispatching
    def dispatch(self) -> None:
        """Start every ready task that can start now.

        Re-entrant calls (a dispatch triggering an event that asks for another
        dispatch) are folded into one more pass of the outer loop instead of
        recursing.
        """
        if self._dispatching:
            self._dispatch_again = True
            return
        self._dispatching = True
        try:
            while True:
                self._dispatch_again = False
                self._dispatch_pass()
                if not self._dispatch_again:
                    break
        finally:
            self._dispatching = False

    def _dispatch_pass(self) -> None:
        view = SchedulingView(
            now=self._context.now,
            experiments=self._state.experiments,
            plates=self._state.plates,
            equipment=self._state.equipment,
            resources=self._resources,
            travel=self._travel,
        )
        ready = self._state.tasks.ready_tasks()
        robots_free = bool(self._resources.available_robots())
        if not robots_free:
            # With every robot busy, only tasks that can run where their plate already is can start;
            # skip ordering and checking the rest (a large backlog would make every pass O(queue)).
            ready = [task for task in ready if self._runs_in_place(task)]
        for task in self._scheduler.order(ready, view):
            # A task started earlier in this pass may have used the last robot or slot,
            # so feasibility is re-checked for each task rather than computed once.
            if task.status is not TaskStatus.READY:
                continue
            if not robots_free and not self._runs_in_place(task):
                continue
            if self._try_start(task, view):
                robots_free = bool(self._resources.available_robots())

    def _runs_in_place(self, task: Task) -> bool:
        plate = self._state.plate(task.plate_id)
        current = self._state.equipment.get(plate.location_id or "")
        return current is not None and current.kind is EQUIPMENT_FOR_OPERATION[task.operation] \
            and current.is_operational and plate.plate_id not in self._active

    def _try_start(self, task: Task, view: SchedulingView) -> bool:
        plate = self._state.plate(task.plate_id)
        if plate.plate_id in self._active:
            return False
        needed = EQUIPMENT_FOR_OPERATION[task.operation]
        current = self._state.equipment_item(plate.location_id or "")

        if current.kind is needed and current.is_operational:
            # e.g. two consecutive INCUBATE steps: no need to move the plate.
            self._mark_running(task, current.equipment_id)
            self._start_operation(task, current.equipment_id)
            return True

        destinations = self._resources.available(needed)
        robots = self._resources.available_robots()
        if not destinations or not robots:
            return False
        destination = self._scheduler.choose_destination(task, destinations, view)
        robot: Robot = self._state.equipment_item(self._scheduler.choose_robot(task, robots, view))  # type: ignore[assignment]

        self._resources.reserve(destination, plate.plate_id, task.task_id)
        self._mark_running(task, destination)
        robot.start_transport(TransportJob(
            plate_id=plate.plate_id,
            source=current,  # type: ignore[arg-type]
            destination=self._state.equipment_item(destination),  # type: ignore[arg-type]
        ))
        self._publish(DispatchEvent.TASK_DISPATCHED, task_id=task.task_id, plate_id=plate.plate_id,
                      operation=task.operation, destination=destination, robot=robot.equipment_id)
        return True

    def _mark_running(self, task: Task, destination: str) -> None:
        self._state.tasks.mark_running(task.task_id, destination, self._context.now)
        self._active[task.plate_id] = _ActiveTask(task.task_id, destination)
        experiment = self._state.experiment(task.experiment_id)
        if experiment.status is ExperimentStatus.SUBMITTED:
            experiment.status = ExperimentStatus.RUNNING

    # ------------------------------------------------------------ bus handlers
    def _on_plate_placed(self, event: Event) -> None:
        active = self._active.get(event.payload["plate_id"])
        if active is None or event.payload["destination"] != active.destination_id:
            return
        if not self._state.equipment_item(active.destination_id).is_operational:
            # The destination failed while the plate was on its way: send it somewhere else.
            self.requeue(active.task_id, f"{active.destination_id} failed before the plate arrived")
            return
        self._start_operation(self._state.tasks.get(active.task_id), active.destination_id)

    # ----------------------------------------------------------------- recovery
    def active_task_for(self, plate_id: str) -> str | None:
        active = self._active.get(plate_id)
        return active.task_id if active else None

    def requeue(self, task_id: str, reason: str, remaining_duration_min: float | None = None) -> None:
        """Put a RUNNING task back to READY so it is dispatched again (used by fault recovery).

        Releases the task's reservation if the plate never arrived. ``remaining_duration_min``
        shortens an interrupted incubation to the time still owed.
        """
        task = self._state.tasks.get(task_id)
        self._active.pop(task.plate_id, None)
        reservation = self._resources.reservation_for_plate(task.plate_id)
        if reservation is not None:
            self._resources.cancel(reservation.reservation_id)
        if remaining_duration_min is not None:
            task.duration_min = max(remaining_duration_min, MIN_REQUEUED_DURATION_MIN)
        self._state.tasks.requeue(task_id, self._context.now)
        self._publish(DispatchEvent.TASK_REQUEUED, task_id=task_id, plate_id=task.plate_id,
                      operation=task.operation, reason=reason)
        self.dispatch()

    def fail_plate(self, plate_id: str, reason: str) -> list[Task]:
        """Give up on a plate: cancel its unfinished work. Returns the cancelled tasks."""
        self._active.pop(plate_id, None)
        reservation = self._resources.reservation_for_plate(plate_id)
        if reservation is not None:
            self._resources.cancel(reservation.reservation_id)
        cancelled: list[Task] = []
        for task in [t for t in self._state.tasks if t.plate_id == plate_id and not t.is_terminal]:
            if task.status is TaskStatus.RUNNING:
                cancelled += self._state.tasks.mark_failed(task.task_id, self._context.now)
            elif not task.is_terminal:
                cancelled += self._state.tasks.cancel(task.task_id)
        logger.warning("gave up on %s: %s", plate_id, reason)
        for experiment_id in {t.experiment_id for t in cancelled}:
            self._finish_experiment_if_done(experiment_id)
        return cancelled

    def _on_processing_completed(self, event: Event) -> None:
        active = self._active.get(event.payload["plate_id"])
        if active is not None and event.source == active.destination_id:
            self._complete(self._state.tasks.get(active.task_id), active.destination_id)

    # ---------------------------------------------------------------- execution
    def _start_operation(self, task: Task, equipment_id: str) -> None:
        equipment = self._state.equipment_item(equipment_id)
        if task.operation is Operation.INCUBATE:
            assert isinstance(equipment, Incubator) and task.duration_min is not None
            equipment.start_incubation(task.plate_id, task.duration_min)
        elif task.operation in (Operation.MEDIA_EXCHANGE, Operation.IMAGE):
            assert isinstance(equipment, ProcessingStation)
            equipment.start_processing(task.plate_id, task.duration_min)
        elif task.operation is Operation.ARCHIVE:
            assert isinstance(equipment, Storage)
            equipment.archive(task.plate_id)
            self._complete(task, equipment_id)
        else:  # DISPOSE: arriving at the waste station is the whole operation
            self._complete(task, equipment_id)

    def _complete(self, task: Task, equipment_id: str) -> None:
        del self._active[task.plate_id]
        self._state.tasks.mark_completed(task.task_id, self._context.now)
        self._publish(DispatchEvent.TASK_COMPLETED, task_id=task.task_id, plate_id=task.plate_id,
                      operation=task.operation, equipment=equipment_id)
        self._finish_experiment_if_done(task.experiment_id)
        self.dispatch()

    def _finish_experiment_if_done(self, experiment_id: str) -> None:
        if not self._state.tasks.is_experiment_finished(experiment_id):
            return
        experiment = self._state.experiment(experiment_id)
        all_completed = all(t.status is TaskStatus.COMPLETED for t in self._state.tasks.tasks_for(experiment_id))
        experiment.status = ExperimentStatus.COMPLETED if all_completed else ExperimentStatus.FAILED
        logger.info("experiment %s %s", experiment_id, experiment.status)
        self._publish(DispatchEvent.EXPERIMENT_FINISHED, experiment_id=experiment_id, status=experiment.status)

    def _publish(self, event_type: DispatchEvent, **payload: Any) -> None:
        self._context.publish(event_type, SOURCE_ID, payload=payload)
