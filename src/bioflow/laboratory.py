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
from bioflow.domain import Experiment
from bioflow.equipment.config import EquipmentConfig
from bioflow.equipment.factory import build_equipment
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
    ) -> None:
        self.engine = SimulationEngine(seed=seed)
        equipment = build_equipment(equipment_config, self.engine)
        self.state = StateManager(equipment)
        self.resources = ResourceManager(equipment, self.engine, self.engine.bus)
        self.dispatcher = Dispatcher(self.state, self.resources, scheduler, travel, self.engine, self.engine.bus)

    def schedule_experiment(self, experiment: Experiment) -> None:
        """Submit ``experiment`` when simulation time reaches its ``submitted_at``."""
        self.engine.schedule_at(
            experiment.submitted_at, EXPERIMENT_ARRIVAL, SOURCE_ID,
            lambda event: self.dispatcher.submit(experiment),
            payload={"experiment_id": experiment.experiment_id},
        )

    def run(self, until: float | None = None) -> RunSummary:
        self.engine.run(until=until)
        return summarize(
            self.state,
            scheduler=self.dispatcher.scheduler.name,
            end_time=self.engine.now,
            events_processed=self.engine.events_processed,
            queue_empty=self.engine.pending_count == 0,
        )
