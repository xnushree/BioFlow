"""A plate's full journey through real equipment, driven only by bus events.

The ``ScriptedController`` is a stand-in for the real scheduler (Phase 10):
it follows one fixed route and reacts to TRANSPORT_COMPLETED and
PROCESSING_COMPLETED notifications. It shows that equipment needs no direct
link to whatever controls it.
"""

from pathlib import Path

import pytest

from bioflow.core.events import Event
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import PlateState
from bioflow.equipment import EquipmentEvent, Incubator, ProcessingStation, Robot, Storage, TransportJob
from bioflow.equipment.config import load_equipment_config
from bioflow.equipment.factory import build_equipment
from bioflow.robotics.motion import TimedMotion
from bioflow.robotics.travel import ConstantTravelTime

CONFIG = Path(__file__).parents[2] / "configs" / "equipment.yaml"
TRAVEL_MIN = 2.0

# (destination, incubation minutes or None for station default / archive)
ROUTE = [
    ("INCUBATOR_01", 720.0),
    ("MEDIA_01", None),
    ("INCUBATOR_01", 1440.0),
    ("IMAGING_01", None),
    ("STORAGE_01", None),
]


class ScriptedController:
    def __init__(self, engine: SimulationEngine, equipment: dict, plate_id: str) -> None:
        self.equipment = equipment
        self.plate_id = plate_id
        self.robot: Robot = equipment["ROBOT_01"]
        self.location = "STORAGE_01"
        self.step = 0
        engine.bus.subscribe(EquipmentEvent.TRANSPORT_COMPLETED, self.on_arrived)
        engine.bus.subscribe(EquipmentEvent.PROCESSING_COMPLETED, self.on_processed)

    def start(self) -> None:
        self.move_to(ROUTE[0][0])

    def move_to(self, destination: str) -> None:
        self.robot.start_transport(TransportJob(
            self.plate_id, self.equipment[self.location], self.equipment[destination]
        ))
        self.location = destination

    def on_arrived(self, event: Event) -> None:
        destination, duration = ROUTE[self.step]
        target = self.equipment[destination]
        if isinstance(target, Incubator):
            target.start_incubation(self.plate_id, duration)
        elif isinstance(target, ProcessingStation):
            target.start_processing(self.plate_id)
        elif isinstance(target, Storage):
            target.archive(self.plate_id)

    def on_processed(self, event: Event) -> None:
        self.step += 1
        self.move_to(ROUTE[self.step][0])


def test_plate_completes_full_protocol_route(engine: SimulationEngine, make_plate, event_log: list[Event]) -> None:
    config = load_equipment_config(CONFIG)
    equipment = build_equipment(config, engine, TimedMotion(engine, ConstantTravelTime(TRAVEL_MIN)))
    plate = make_plate("EXP001-P001")
    equipment["STORAGE_01"].receive(plate)

    ScriptedController(engine, equipment, plate.plate_id).start()
    engine.run()

    robot_spec = config.robots.spec
    # The robot is always already at the pickup point, so only the loaded leg takes travel time.
    per_transport = TRAVEL_MIN + robot_spec.pick_time_min + robot_spec.place_time_min
    processing = (720 + config.media_stations.spec.process_time_min
                  + 1440 + config.imaging_stations.spec.process_time_min)
    assert engine.now == pytest.approx(5 * per_transport + processing)

    assert plate.state is PlateState.ARCHIVED
    assert plate.location_id == "OFFSITE_ARCHIVE"  # archived plates leave the automated lab
    processed = [e.payload["operation"] for e in event_log
                 if e.event_type == EquipmentEvent.PROCESSING_COMPLETED]
    assert processed == ["INCUBATE", "MEDIA_EXCHANGE", "INCUBATE", "IMAGE"]
    assert all(eq.snapshot()["state"] in ("IDLE", "AVAILABLE") for eq in equipment.values())
