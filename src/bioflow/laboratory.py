"""Composition root: builds and wires one complete simulated laboratory.

This is the only place that knows how all components fit together. Every
component receives its collaborators here (dependency injection), so each
one can also be built on its own in tests.
"""

from __future__ import annotations

from bioflow.analytics.summary import RunSummary, summarize
from bioflow.control.dispatcher import Dispatcher
from bioflow.control.resource_manager import ResourceManager
from bioflow.control.state_manager import StateManager
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import EquipmentKind, Experiment
from bioflow.equipment.config import EquipmentConfig
from bioflow.equipment.factory import build_equipment
from bioflow.equipment.robot import Robot
from bioflow.faults.config import FaultConfig
from bioflow.faults.diagnostics import evaluate_detection
from bioflow.faults.fault import Fault, FaultSpec
from bioflow.faults.fault_detector import ExpectedTransport, FaultDetector
from bioflow.faults.fault_injector import FaultInjector
from bioflow.faults.monitoring import EquipmentMonitor
from bioflow.faults.recovery import RecoveryManager
from bioflow.robotics.layout import LabLayout
from bioflow.telemetry.metrics import MetricsCollector
from bioflow.telemetry.recorder import TelemetryLevel, TelemetryRecorder
from bioflow.robotics.motion import GridMotion, MotionController, TimedMotion
from bioflow.robotics.travel import TravelTimeModel
from bioflow.scheduling.base_scheduler import Scheduler

EXPERIMENT_ARRIVAL = "EXPERIMENT_ARRIVAL"
SOURCE_ID = "LABORATORY"


class Laboratory:
    def __init__(
        self,
        equipment_config: EquipmentConfig,
        scheduler: Scheduler,
        travel: TravelTimeModel,
        seed: int = 0,
        layout: LabLayout | None = None,
        fault_config: FaultConfig | None = None,
        telemetry: TelemetryLevel | None = None,
    ) -> None:
        """With a ``layout``, robots move cell by cell with reservations and deadlock handling
        (GridMotion); without one, each trip is a single timed event (TimedMotion).

        ``fault_config`` sets monitoring and detection thresholds (defaults if omitted).
        ``telemetry`` turns on structured event recording at that detail level (off by default).
        """
        self.engine = SimulationEngine(seed=seed)
        self.layout = layout
        self.motion: MotionController
        if layout is None:
            self.motion = TimedMotion(self.engine, travel)
        else:
            self.motion = GridMotion(self.engine, layout.map, layout.robot_speed_m_per_min, layout.map.parking)
        equipment = build_equipment(equipment_config, self.engine, self.motion)
        if layout is not None:
            layout.map.check_covers(eid for eid, eq in equipment.items() if eq.kind is not EquipmentKind.ROBOT)
        self.state = StateManager(equipment)
        self.resources = ResourceManager(equipment, self.engine, self.engine.bus)
        self.dispatcher = Dispatcher(self.state, self.resources, scheduler, travel, self.engine, self.engine.bus)
        fault_config = fault_config or FaultConfig()
        self.injector = FaultInjector(self.engine, equipment, self.motion)
        self.detector = FaultDetector(
            self.engine, self.engine.bus, {eid: eq.kind for eid, eq in equipment.items()},
            fault_config.detection, expected_transport=self._expected_transport_factory(travel),
            heartbeat_interval_min=fault_config.monitoring.heartbeat_interval_min,
            nominal_step_min=layout.map.cell_size_m / layout.robot_speed_m_per_min if layout else None,
        )
        self.recovery = RecoveryManager(
            self.engine, self.engine.bus, self.state, self.resources, self.dispatcher, self.detector,
            fault_config.recovery,
        )
        self.metrics = MetricsCollector(self.engine.bus, equipment, ready_count=lambda: self.state.tasks.ready_count)
        self.recorder = TelemetryRecorder(self.engine.bus, telemetry) if telemetry is not None else None
        self.monitor = EquipmentMonitor(
            self.engine, equipment, self.engine.rng, fault_config.monitoring,
            other_events_pending=lambda: self.engine.pending_count > 0,
            work_remaining=lambda: any(not task.is_terminal for task in self.state.tasks),
        )

    def _expected_transport_factory(self, travel: TravelTimeModel) -> ExpectedTransport:
        """Nominal transport duration, used by the detector to decide what counts as overdue."""
        def expected(robot_id: str, source: str, destination: str) -> float:
            robot = self.state.equipment_item(robot_id)
            assert isinstance(robot, Robot)
            return (travel.travel_time(robot.location_id, source) + robot.spec.pick_time_min
                    + travel.travel_time(source, destination) + robot.spec.place_time_min)
        return expected

    def schedule_experiment(self, experiment: Experiment) -> None:
        """Submit ``experiment`` when simulation time reaches its ``submitted_at``."""
        self.engine.schedule_at(
            experiment.submitted_at, EXPERIMENT_ARRIVAL, SOURCE_ID,
            lambda event: self._arrive(experiment),
            payload={"experiment_id": experiment.experiment_id},
        )
        self.monitor.start()

    def schedule_fault(self, spec: FaultSpec) -> Fault:
        """Plan a hidden hardware fault (see bioflow.faults.fault_injector)."""
        fault = self.injector.schedule(spec)
        self.monitor.start()
        return fault

    def _arrive(self, experiment: Experiment) -> None:
        self.dispatcher.submit(experiment)
        self.monitor.start()  # it may have stopped while the lab was idle

    def run(self, until: float | None = None) -> RunSummary:
        self.engine.run(until=until)
        completed = [t.completed_at for t in self.state.tasks if t.completed_at is not None]
        return summarize(
            self.state,
            scheduler=self.dispatcher.scheduler.name,
            end_time=self.engine.now,
            events_processed=self.engine.events_processed,
            queue_empty=self.engine.pending_count == 0,
            motion=self.motion.stats if isinstance(self.motion, GridMotion) else None,
            faults=evaluate_detection(self.injector.faults, self.detector.detections)
            if self.injector.faults else None,
            recovery=self.recovery.report() if self.recovery.recoveries else None,
            metrics=self.metrics.finalize(self.state, max(completed, default=self.engine.now)),
        )
