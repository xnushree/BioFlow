"""Two plates compete for one imaging station, coordinated through reservations.

This closes the Phase 5 gap: without reservations, the second robot would
travel to a full station and fail on arrival. With them, the second plate
waits in storage until RESOURCE_AVAILABLE says the station has room.
"""

import pytest

from bioflow.control.resource_manager import ResourceEvent, ResourceManager
from bioflow.core.events import Event
from bioflow.core.exceptions import CapacityExceededError
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import EquipmentKind, Operation, PlateState
from bioflow.equipment import EquipmentEvent, ProcessingStation, Robot, Storage, TransportJob
from bioflow.equipment.config import ContainerSpec, RobotSpec, StationSpec
from bioflow.robotics.motion import TimedMotion
from bioflow.robotics.travel import ConstantTravelTime

TRAVEL_MIN = 2.0


def test_second_plate_waits_for_station_instead_of_colliding(
    engine: SimulationEngine, make_plate, event_log: list[Event]
) -> None:
    storage = Storage("STORAGE_01", engine, ContainerSpec(10))
    imager = ProcessingStation("IMAGING_01", engine, Operation.IMAGE, StationSpec(10))
    motion = TimedMotion(engine, ConstantTravelTime(TRAVEL_MIN))
    robots = [Robot(f"ROBOT_0{i}", engine, RobotSpec(0.5, 0.5), "STORAGE_01", motion) for i in (1, 2)]
    lab = {eq.equipment_id: eq for eq in (storage, imager, *robots)}
    manager = ResourceManager(lab, engine, engine.bus)
    for plate_id in ("P1", "P2"):
        storage.receive(make_plate(plate_id, state=PlateState.CREATED))

    waiting = ["P1", "P2"]  # stand-in for the scheduler's ready queue

    def dispatch() -> None:
        """Send the next waiting plate if a station AND a robot are free."""
        if not waiting:
            return
        stations = manager.available(EquipmentKind.IMAGING_STATION)
        idle_robots = manager.available_robots()
        if not stations or not idle_robots:
            return
        plate_id = waiting.pop(0)
        manager.reserve(stations[0], plate_id)
        lab[idle_robots[0]].start_transport(
            TransportJob(plate_id, storage, lab[stations[0]])
        )

    def on_placed(event: Event) -> None:
        if event.payload["destination"] == "IMAGING_01":
            imager.start_processing(event.payload["plate_id"])

    def on_imaged(event: Event) -> None:  # send the imaged plate back to storage
        idle = manager.available_robots()[0]
        manager.reserve("STORAGE_01", event.payload["plate_id"])
        lab[idle].start_transport(TransportJob(event.payload["plate_id"], imager, storage))

    engine.bus.subscribe(EquipmentEvent.PLATE_PLACED, on_placed)
    engine.bus.subscribe(EquipmentEvent.PROCESSING_COMPLETED, on_imaged)
    engine.bus.subscribe(ResourceEvent.AVAILABLE, lambda e: dispatch())

    dispatch()
    # P2 could not be dispatched: the only station is reserved for P1.
    assert waiting == ["P2"]
    with pytest.raises(CapacityExceededError):
        manager.reserve("IMAGING_01", "P2")

    engine.run()

    imaged = [(e.timestamp, e.payload["plate_id"]) for e in event_log
              if e.event_type == EquipmentEvent.PROCESSING_COMPLETED]
    assert [plate for _, plate in imaged] == ["P1", "P2"]
    assert imaged[1][0] > imaged[0][0] + 10  # P2 imaged strictly after P1 left the station
    assert storage.occupancy == 2
    assert manager.status("IMAGING_01").free == 1
