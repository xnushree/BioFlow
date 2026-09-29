"""Tests for Incubator."""

import pytest

from bioflow.core.events import Event
from bioflow.core.exceptions import CapacityExceededError, SafetyViolationError, UnknownEntityError
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import Operation, PlateState
from bioflow.equipment import EquipmentEvent, Incubator, IncubatorState
from bioflow.equipment.config import IncubatorSpec


@pytest.fixture
def incubator(engine: SimulationEngine) -> Incubator:
    return Incubator("INCUBATOR_01", engine, IncubatorSpec(capacity=2))


def test_receive_places_plate_inside(incubator: Incubator, make_plate, event_log: list[Event]) -> None:
    plate = make_plate("P1")

    incubator.receive(plate)

    assert incubator.holds("P1")
    assert plate.location_id == "INCUBATOR_01"
    assert plate.state is PlateState.WAITING
    assert event_log[-1].event_type == EquipmentEvent.PLATE_RECEIVED
    assert event_log[-1].payload == {"plate_id": "P1"}


def test_state_tracks_occupancy(incubator: Incubator, make_plate, event_log: list[Event]) -> None:
    incubator.receive(make_plate("P1"))
    assert incubator.state is IncubatorState.AVAILABLE

    incubator.receive(make_plate("P2"))
    assert incubator.state is IncubatorState.FULL

    incubator.release("P1")
    assert incubator.state is IncubatorState.AVAILABLE
    changes = [(e.payload["from_state"], e.payload["to_state"])
               for e in event_log if e.event_type == EquipmentEvent.STATE_CHANGED]
    assert changes == [("AVAILABLE", "FULL"), ("FULL", "AVAILABLE")]


def test_capacity_is_enforced(incubator: Incubator, make_plate) -> None:
    incubator.receive(make_plate("P1"))
    incubator.receive(make_plate("P2"))

    with pytest.raises(CapacityExceededError):
        incubator.receive(make_plate("P3"))


def test_incubation_completes_after_duration(
    engine: SimulationEngine, incubator: Incubator, make_plate, event_log: list[Event]
) -> None:
    plate = make_plate("P1")
    incubator.receive(plate)

    incubator.start_incubation("P1", duration_min=720)
    assert plate.state is PlateState.INCUBATING
    assert incubator.is_incubating("P1")

    engine.run()

    assert engine.now == 720
    assert plate.state is PlateState.WAITING
    assert not incubator.is_incubating("P1")
    completed = event_log[-1]
    assert completed.event_type == EquipmentEvent.PROCESSING_COMPLETED
    assert completed.timestamp == 720
    assert completed.payload == {"plate_id": "P1", "operation": Operation.INCUBATE}


def test_plates_incubate_independently(
    engine: SimulationEngine, incubator: Incubator, make_plate, event_log: list[Event]
) -> None:
    for plate_id, duration in (("P1", 300), ("P2", 100)):
        incubator.receive(make_plate(plate_id))
        incubator.start_incubation(plate_id, duration)

    engine.run()

    done = [(e.timestamp, e.payload["plate_id"]) for e in event_log
            if e.event_type == EquipmentEvent.PROCESSING_COMPLETED]
    assert done == [(100, "P2"), (300, "P1")]


def test_cannot_remove_plate_while_incubating(incubator: Incubator, make_plate) -> None:
    incubator.receive(make_plate("P1"))
    incubator.start_incubation("P1", 60)

    with pytest.raises(SafetyViolationError, match="while it is incubating"):
        incubator.release("P1")
    assert incubator.holds("P1")


def test_cannot_start_incubation_twice(incubator: Incubator, make_plate) -> None:
    incubator.receive(make_plate("P1"))
    incubator.start_incubation("P1", 60)

    with pytest.raises(SafetyViolationError, match="already incubating"):
        incubator.start_incubation("P1", 60)


def test_cannot_incubate_a_plate_that_is_not_inside(incubator: Incubator) -> None:
    with pytest.raises(UnknownEntityError):
        incubator.start_incubation("P404", 60)


def test_environment_starts_at_setpoint(engine: SimulationEngine) -> None:
    incubator = Incubator("INC", engine, IncubatorSpec(capacity=1, temperature_c=30.0, co2_pct=3.0))

    assert (incubator.temperature_c, incubator.co2_pct) == (30.0, 3.0)
    assert incubator.setpoint.is_within(incubator.temperature_c, incubator.co2_pct)


def test_snapshot(incubator: Incubator, make_plate) -> None:
    incubator.receive(make_plate("P1"))
    incubator.start_incubation("P1", 60)

    snap = incubator.snapshot()

    assert snap["equipment_id"] == "INCUBATOR_01"
    assert snap["kind"] == "INCUBATOR"
    assert snap["occupancy"] == 1
    assert snap["incubating"] == ["P1"]
