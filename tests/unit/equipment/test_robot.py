"""Tests for Robot transport."""

import pytest

from bioflow.core.events import Event
from bioflow.core.exceptions import CapacityExceededError, ResourceUnavailableError, ValidationError
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import Operation, PlateState
from bioflow.equipment import EquipmentEvent, ProcessingStation, Robot, RobotState, Storage, TransportJob
from bioflow.equipment.config import ContainerSpec, RobotSpec, StationSpec

PICK_MIN = 0.5
PLACE_MIN = 1.0


@pytest.fixture
def storage(engine: SimulationEngine) -> Storage:
    return Storage("STORAGE_01", engine, ContainerSpec(capacity=10))


@pytest.fixture
def imager(engine: SimulationEngine) -> ProcessingStation:
    return ProcessingStation("IMAGING_01", engine, Operation.IMAGE, StationSpec(10))


@pytest.fixture
def robot(engine: SimulationEngine) -> Robot:
    return Robot("ROBOT_01", engine, RobotSpec(PICK_MIN, PLACE_MIN), home_location_id="STORAGE_01")


def job(plate_id: str, source, destination, to_source: float = 2.0, to_dest: float = 3.0) -> TransportJob:
    return TransportJob(plate_id, source, destination, to_source, to_dest)


def test_transport_moves_plate_with_correct_timing(
    engine: SimulationEngine, robot: Robot, storage: Storage, imager: ProcessingStation,
    make_plate, event_log: list[Event],
) -> None:
    plate = make_plate("P1")
    storage.receive(plate)

    robot.start_transport(job("P1", storage, imager, to_source=2.0, to_dest=3.0))
    engine.run()

    # 2.0 travel + 0.5 pick + 3.0 travel + 1.0 place
    assert engine.now == pytest.approx(6.5)
    assert imager.holds("P1") and not storage.holds("P1")
    assert plate.location_id == "IMAGING_01"
    assert plate.state is PlateState.WAITING
    assert robot.is_idle and robot.carrying is None and robot.job is None
    assert robot.location_id == "IMAGING_01"

    milestones = [(e.timestamp, e.event_type) for e in event_log
                  if e.source == "ROBOT_01" and e.event_type != EquipmentEvent.STATE_CHANGED]
    assert milestones == [
        (0.0, EquipmentEvent.TRANSPORT_STARTED),
        (2.5, EquipmentEvent.PLATE_PICKED),
        (6.5, EquipmentEvent.PLATE_PLACED),
        (6.5, EquipmentEvent.TRANSPORT_COMPLETED),
    ]


def test_robot_passes_through_every_state_in_order(
    engine: SimulationEngine, robot: Robot, storage: Storage, imager: ProcessingStation,
    make_plate, event_log: list[Event],
) -> None:
    storage.receive(make_plate("P1"))

    robot.start_transport(job("P1", storage, imager))
    engine.run()

    states = [e.payload["to_state"] for e in event_log
              if e.source == "ROBOT_01" and e.event_type == EquipmentEvent.STATE_CHANGED]
    assert states == ["ASSIGNED", "MOVING", "PICKING", "TRANSPORTING", "PLACING", "IDLE"]


def test_plate_is_in_transit_while_carried(
    engine: SimulationEngine, robot: Robot, storage: Storage, imager: ProcessingStation, make_plate
) -> None:
    plate = make_plate("P1")
    storage.receive(plate)
    robot.start_transport(job("P1", storage, imager, to_source=2.0, to_dest=3.0))

    engine.run(until=4.0)  # after pick (2.5), before arrival (5.5)

    assert robot.state is RobotState.TRANSPORTING
    assert robot.carrying is plate
    assert plate.state is PlateState.IN_TRANSIT
    assert plate.location_id == "ROBOT_01"


def test_busy_robot_rejects_a_second_job(
    robot: Robot, storage: Storage, imager: ProcessingStation, make_plate
) -> None:
    storage.receive(make_plate("P1"))
    storage.receive(make_plate("P2"))
    robot.start_transport(job("P1", storage, imager))

    with pytest.raises(ResourceUnavailableError, match="busy"):
        robot.start_transport(job("P2", storage, imager))


def test_placing_into_full_destination_fails_loudly(
    engine: SimulationEngine, robot: Robot, storage: Storage, imager: ProcessingStation, make_plate
) -> None:
    # Without reservations (Phase 7) nothing stops this, so it must at least not pass silently.
    imager.receive(make_plate("BLOCKER"))
    storage.receive(make_plate("P1"))
    robot.start_transport(job("P1", storage, imager))

    with pytest.raises(CapacityExceededError):
        engine.run()


@pytest.mark.parametrize(("to_source", "to_dest"), [(-1.0, 1.0), (1.0, float("inf"))])
def test_job_rejects_invalid_travel_times(
    storage: Storage, imager: ProcessingStation, to_source: float, to_dest: float
) -> None:
    with pytest.raises(ValidationError):
        job("P1", storage, imager, to_source, to_dest)


def test_job_rejects_same_source_and_destination(storage: Storage) -> None:
    with pytest.raises(ValidationError, match="source and destination"):
        job("P1", storage, storage)
