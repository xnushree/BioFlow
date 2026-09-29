"""Tests for ProcessingStation (media exchange and imaging)."""

import pytest

from bioflow.core.events import Event
from bioflow.core.exceptions import CapacityExceededError, SafetyViolationError, ValidationError
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import EquipmentKind, Operation, PlateState
from bioflow.equipment import EquipmentEvent, ProcessingStation, StationState
from bioflow.equipment.config import StationSpec


@pytest.fixture
def imager(engine: SimulationEngine) -> ProcessingStation:
    return ProcessingStation("IMAGING_01", engine, Operation.IMAGE, StationSpec(process_time_min=10))


def test_kind_follows_operation(engine: SimulationEngine) -> None:
    media = ProcessingStation("MEDIA_01", engine, Operation.MEDIA_EXCHANGE, StationSpec(15))

    assert media.kind is EquipmentKind.MEDIA_STATION


@pytest.mark.parametrize("operation", [Operation.INCUBATE, Operation.TRANSPORT, Operation.ARCHIVE])
def test_rejects_non_station_operations(engine: SimulationEngine, operation: Operation) -> None:
    with pytest.raises(ValidationError, match="cannot perform"):
        ProcessingStation("X", engine, operation, StationSpec(5))


def test_full_processing_cycle(
    engine: SimulationEngine, imager: ProcessingStation, make_plate, event_log: list[Event]
) -> None:
    plate = make_plate("P1")
    imager.receive(plate)
    assert imager.state is StationState.OCCUPIED

    imager.start_processing("P1")
    assert imager.state is StationState.PROCESSING
    assert plate.state is PlateState.PROCESSING

    engine.run()

    assert engine.now == 10  # configured process_time_min
    assert imager.state is StationState.OCCUPIED
    assert plate.state is PlateState.WAITING

    imager.release("P1")
    assert imager.state is StationState.IDLE
    assert [e.event_type for e in event_log if e.event_type != EquipmentEvent.STATE_CHANGED] == [
        EquipmentEvent.PLATE_RECEIVED,
        EquipmentEvent.PROCESSING_STARTED,
        EquipmentEvent.PROCESSING_COMPLETED,
        EquipmentEvent.PLATE_RELEASED,
    ]


def test_explicit_duration_overrides_config(engine: SimulationEngine, imager: ProcessingStation, make_plate) -> None:
    imager.receive(make_plate("P1"))

    imager.start_processing("P1", duration_min=3)
    engine.run()

    assert engine.now == 3


def test_holds_only_one_plate(imager: ProcessingStation, make_plate) -> None:
    imager.receive(make_plate("P1"))

    with pytest.raises(CapacityExceededError):
        imager.receive(make_plate("P2"))


def test_cannot_remove_plate_during_processing(imager: ProcessingStation, make_plate) -> None:
    imager.receive(make_plate("P1"))
    imager.start_processing("P1")

    with pytest.raises(SafetyViolationError, match="during IMAGE"):
        imager.release("P1")


def test_cannot_start_processing_twice(imager: ProcessingStation, make_plate) -> None:
    imager.receive(make_plate("P1"))
    imager.start_processing("P1")

    with pytest.raises(SafetyViolationError, match="already processing"):
        imager.start_processing("P1")
