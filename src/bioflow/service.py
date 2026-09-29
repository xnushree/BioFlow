"""Application service: one live laboratory that interfaces (REST API, dashboard) can drive and observe.

The simulation runs in a background thread in slices of simulated time. A
single lock is held while a slice runs and while any read is made, so an
observer always sees a consistent state and never a half-processed event.

    speed = simulated minutes per real second (e.g. 60: one simulated hour per
            second), or None to run as fast as possible.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

from bioflow.analytics.summary import RunSummary
from bioflow.core.exceptions import ConfigurationError, SimulationError, UnknownEntityError, ValidationError
from bioflow.core.validation import require
from bioflow.domain import Experiment, TaskStatus
from bioflow.faults.fault import FaultSpec
from bioflow.laboratory import Laboratory
from bioflow.protocols import load_protocol_library
from bioflow.robotics.motion import GridMotion
from bioflow.scenario import Scenario, build_laboratory, load_scenario
from bioflow.telemetry.recorder import TelemetryLevel, json_safe

T = TypeVar("T")

FAST_SLICE_MIN = 60.0  # simulated minutes per slice when running as fast as possible
PACED_TICK_S = 0.1  # real seconds between slices when running at a set speed


class RunState(StrEnum):
    EMPTY = "EMPTY"  # nothing loaded
    READY = "READY"  # loaded, not started
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    FINISHED = "FINISHED"  # nothing left to simulate


@dataclass(frozen=True)
class Status:
    state: RunState
    scenario: str | None
    scheduler: str | None
    sim_time: float
    speed: float | None
    events_processed: int
    tasks_total: int
    tasks_completed: int
    error: str | None = None


class SimulationService:
    def __init__(self, telemetry: TelemetryLevel = TelemetryLevel.STANDARD) -> None:
        self._telemetry = telemetry
        self._lock = threading.RLock()
        self._lab: Laboratory | None = None
        self._scenario: Scenario | None = None
        self._state = RunState.EMPTY
        self._speed: float | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._error: str | None = None

    # ---------------------------------------------------------------- control
    def load(self, scenario_path: Path, scheduler: str | None = None) -> Status:
        """Build a fresh laboratory from a scenario file (stopping any current run)."""
        scenario = load_scenario(scenario_path)
        lab = build_laboratory(scenario, scheduler=scheduler, telemetry=self._telemetry)
        self.pause()
        with self._lock:
            self._lab, self._scenario, self._state, self._error = lab, scenario, RunState.READY, None
        return self.status()

    def start(self, speed: float | None = None) -> Status:
        """Run in the background at ``speed`` simulated minutes per real second (None = flat out)."""
        require(speed is None or speed > 0, f"speed must be positive, got {speed}")
        with self._lock:
            self._require_lab()
            if self._state is RunState.FINISHED:
                raise SimulationError("the simulation has finished; load it again to rerun")
            if self._state is RunState.RUNNING:
                self._speed = speed
                return self.status()
            self._speed, self._state = speed, RunState.RUNNING
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="bioflow-simulation", daemon=True)
        self._thread.start()
        return self.status()

    def pause(self) -> Status:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        self._thread = None
        with self._lock:
            if self._state is RunState.RUNNING:
                self._state = RunState.PAUSED
        return self.status()

    def step(self, minutes: float) -> Status:
        """Advance synchronously by ``minutes`` of simulated time (only while not running)."""
        require(minutes > 0, f"minutes must be positive, got {minutes}")
        with self._lock:
            lab = self._require_lab()
            if self._state is RunState.RUNNING:
                raise SimulationError("pause the simulation before stepping it manually")
            self._advance(lab, minutes)
            if self._state is RunState.READY:
                self._state = RunState.PAUSED
        return self.status()

    def run_to_completion(self) -> RunSummary:
        """Run synchronously until nothing is left to simulate (used by scripts and tests)."""
        with self._lock:
            lab = self._require_lab()
            summary = lab.run()
            self._state = RunState.FINISHED
            return summary

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                with self._lock:
                    lab = self._require_lab()
                    slice_min = FAST_SLICE_MIN if self._speed is None else self._speed * PACED_TICK_S
                    finished = self._advance(lab, slice_min)
                if finished:
                    return
                time.sleep(0 if self._speed is None else PACED_TICK_S)
        except Exception as error:  # surface background failures through status() instead of dying silently
            with self._lock:
                self._error = f"{type(error).__name__}: {error}"
                self._state = RunState.PAUSED

    def _advance(self, lab: Laboratory, minutes: float) -> bool:
        lab.engine.run(until=lab.engine.now + minutes)
        if lab.engine.pending_count == 0:
            self._state = RunState.FINISHED
            return True
        return False

    # ---------------------------------------------------------------- actions
    def submit_experiment(self, experiment_id: str, protocol: str, plates: int, priority: int = 0,
                          deadline_in_min: float | None = None) -> dict[str, Any]:
        """Submit a new experiment at the current simulation time."""
        with self._lock:
            lab = self._require_lab()
            scenario = self._scenario
            assert scenario is not None
            library = load_protocol_library(scenario.protocol_dir)
            if protocol not in library:
                raise UnknownEntityError(protocol, "protocol")
            if experiment_id in lab.state.experiments:
                raise ValidationError(f"Experiment {experiment_id} already exists")
            now = lab.engine.now
            experiment = Experiment(experiment_id, library[protocol], plates, now, priority=priority,
                                    deadline=now + deadline_in_min if deadline_in_min is not None else None)
            lab.schedule_experiment(experiment)
            if self._state is RunState.FINISHED:
                self._state = RunState.PAUSED  # there is work again
            return {"experiment_id": experiment_id, "submitted_at": now}

    def inject_fault(self, spec: FaultSpec) -> dict[str, Any]:
        """Inject a fault starting now (the spec's start time is replaced by the current time)."""
        with self._lock:
            lab = self._require_lab()
            now = lab.engine.now
            fault = lab.schedule_fault(FaultSpec(spec.fault_type, spec.equipment_id, now, spec.duration_min,
                                                 spec.severity, spec.magnitude, spec.metadata))
            if self._state is RunState.FINISHED:
                self._state = RunState.PAUSED
            return {"fault_id": fault.fault_id, "starts_at": now}

    # ---------------------------------------------------------------- queries
    def status(self) -> Status:
        with self._lock:
            lab = self._lab
            tasks = list(lab.state.tasks) if lab else []
            return Status(
                state=self._state,
                scenario=self._scenario.name if self._scenario else None,
                scheduler=lab.dispatcher.scheduler.name if lab else None,
                sim_time=lab.engine.now if lab else 0.0,
                speed=self._speed,
                events_processed=lab.engine.events_processed if lab else 0,
                tasks_total=len(tasks),
                tasks_completed=sum(t.status is TaskStatus.COMPLETED for t in tasks),
                error=self._error,
            )

    def read(self, query: Callable[[Laboratory], T]) -> T:
        """Run ``query`` against the live laboratory under the lock (for consistent snapshots)."""
        with self._lock:
            return query(self._require_lab())

    def experiments(self) -> list[dict[str, Any]]:
        return self.read(lambda lab: [self._experiment_view(lab, eid) for eid in lab.state.experiments])

    def experiment(self, experiment_id: str) -> dict[str, Any]:
        return self.read(lambda lab: self._experiment_view(lab, experiment_id, detailed=True))

    def plates(self, experiment_id: str | None = None, state: str | None = None) -> list[dict[str, Any]]:
        def query(lab: Laboratory) -> list[dict[str, Any]]:
            return [
                {"plate_id": p.plate_id, "experiment_id": p.experiment_id, "cell_type": p.cell_type,
                 "state": p.state, "location_id": p.location_id, "contamination": p.contamination}
                for p in lab.state.plates.values()
                if (experiment_id is None or p.experiment_id == experiment_id) and (state is None or p.state == state)
            ]
        return json_safe(self.read(query))

    def equipment(self) -> list[dict[str, Any]]:
        return json_safe(self.read(lambda lab: [self._equipment_view(lab, eid) for eid in lab.state.equipment]))

    def equipment_item(self, equipment_id: str) -> dict[str, Any]:
        return json_safe(self.read(lambda lab: self._equipment_view(lab, equipment_id)))

    def robots(self) -> list[dict[str, Any]]:
        """Robots with their grid position and remaining planned path (map-based runs)."""
        def query(lab: Laboratory) -> list[dict[str, Any]]:
            motion = lab.motion if isinstance(lab.motion, GridMotion) else None
            return [
                {**self._equipment_view(lab, eid),
                 "cell": motion.position(eid) if motion else None,
                 "path": motion.planned_path(eid) if motion else []}
                for eid, eq in lab.state.equipment.items() if eq.kind == "ROBOT"
            ]
        return json_safe(self.read(query))

    def faults(self) -> dict[str, list[dict[str, Any]]]:
        def query(lab: Laboratory) -> dict[str, list[dict[str, Any]]]:
            return {
                "detections": [
                    {"detection_id": d.detection_id, "fault_type": d.fault_type, "equipment_id": d.equipment_id,
                     "detected_at": d.detected_at, "cleared_at": d.cleared_at, "active": d.active,
                     "evidence": d.evidence}
                    for d in lab.detector.detections
                ],
                "recoveries": [
                    {"recovery_id": r.recovery_id, "detection_id": r.detection_id, "fault_type": r.fault_type,
                     "equipment_id": r.equipment_id, "started_at": r.started_at, "completed_at": r.completed_at,
                     "duration_min": r.duration_min, "blocked_reason": r.blocked_reason,
                     "unrecoverable": r.unrecoverable, "affected_plates": r.affected_plates,
                     "rescheduled_tasks": r.rescheduled_tasks, "actions": r.actions}
                    for r in lab.recovery.recoveries
                ],
                # Ground truth is shown separately and labelled: operators would not normally see it.
                "injected_ground_truth": [
                    {"fault_id": f.fault_id, "fault_type": f.fault_type, "equipment_id": f.equipment_id,
                     "injected_at": f.injected_at, "repaired_at": f.repaired_at, "status": f.status}
                    for f in lab.injector.faults
                ],
            }
        return json_safe(self.read(query))

    def metrics(self) -> dict[str, Any]:
        def query(lab: Laboratory) -> dict[str, Any]:
            completed = [t.completed_at for t in lab.state.tasks if t.completed_at is not None]
            metrics = lab.metrics.finalize(lab.state, max(completed, default=lab.engine.now))
            return {
                **metrics.as_dict(),
                "robot_utilization": metrics.robot_utilization,
                "station_utilization": metrics.station_utilization,
                "incubator_utilization": metrics.incubator_utilization,
                "resources": lab.resources.snapshot(),
                "bus": {"total_published": lab.engine.bus.stats.total_published,
                        "handler_errors": lab.engine.bus.stats.handler_errors},
            }
        return json_safe(self.read(query))

    def events(self, event_type: str | None = None, source: str | None = None,
               limit: int = 100) -> list[dict[str, Any]]:
        """The most recent recorded events, newest last."""
        def query(lab: Laboratory) -> list[dict[str, Any]]:
            records = lab.recorder.records if lab.recorder else []
            matching = [r for r in records if (event_type is None or r["event_type"] == event_type)
                        and (source is None or r["source"] == source)]
            return matching[-limit:]
        return self.read(query)

    # ------------------------------------------------------------------ views
    @staticmethod
    def _experiment_view(lab: Laboratory, experiment_id: str, detailed: bool = False) -> dict[str, Any]:
        experiment = lab.state.experiment(experiment_id)
        progress = lab.state.tasks.progress(experiment_id)
        view: dict[str, Any] = {
            "experiment_id": experiment_id, "protocol": experiment.protocol.name,
            "plate_count": experiment.plate_count, "priority": experiment.priority,
            "submitted_at": experiment.submitted_at, "deadline": experiment.deadline,
            "status": experiment.status, "late": experiment.is_late(lab.engine.now) and not
            lab.state.tasks.is_experiment_finished(experiment_id),
            "tasks": {str(status): count for status, count in progress.items()},
        }
        if detailed:
            view["plates"] = [p.plate_id for p in lab.state.plates.values() if p.experiment_id == experiment_id]
            view["steps"] = [{"operation": s.operation, "duration_min": s.duration_min,
                              "synchronize": s.synchronize} for s in experiment.protocol.steps]
        return json_safe(view)

    @staticmethod
    def _equipment_view(lab: Laboratory, equipment_id: str) -> dict[str, Any]:
        equipment = lab.state.equipment_item(equipment_id)
        return {
            **equipment.snapshot(),
            "operational": equipment.is_operational,
            "in_service": lab.resources.in_service(equipment_id),
            "active_detections": [d.fault_type for d in lab.detector.active_detections(equipment_id)],
        }

    def _require_lab(self) -> Laboratory:
        if self._lab is None:
            raise ConfigurationError("no simulation loaded; POST /simulation/load first")
        return self._lab
