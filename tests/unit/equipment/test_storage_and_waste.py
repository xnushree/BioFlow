"""Tests for Storage and WasteStation."""

import pytest

from bioflow.core.events import Event
from bioflow.core.exceptions import SafetyViolationError
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import PlateState
from bioflow.equipment import EquipmentEvent, Storage, StorageState, WasteStation
from bioflow.equipment.config import ContainerSpec


def test_storage_stores_and_releases_plates(engine: SimulationEngine, make_plate) -> None:
    storage = Storage("STORAGE_01", engine, ContainerSpec(capacity=10))
    plate = make_plate("P1")

    storage.receive(plate)
    assert plate.state is PlateState.STORED

    assert storage.release("P1") is plate
    assert storage.occupancy == 0


def test_storage_becomes_full(engine: SimulationEngine, make_plate) -> None:
    storage = Storage("STORAGE_01", engine, ContainerSpec(capacity=1))

    storage.receive(make_plate("P1"))

    assert storage.state is StorageState.FULL


def test_archived_plates_stay_in_storage(engine: SimulationEngine, make_plate, event_log: list[Event]) -> None:
    storage = Storage("STORAGE_01", engine, ContainerSpec(capacity=10))
    plate = make_plate("P1")
    storage.receive(plate)

    storage.archive("P1")

    assert plate.state is PlateState.ARCHIVED
    assert plate.is_finished
    assert event_log[-1].event_type == EquipmentEvent.PLATE_ARCHIVED
    with pytest.raises(SafetyViolationError, match="archived"):
        storage.release("P1")


def test_waste_station_disposes_plates_permanently(engine: SimulationEngine, make_plate) -> None:
    waste = WasteStation("WASTE_01", engine, ContainerSpec(capacity=5))
    plate = make_plate("P1")

    waste.receive(plate)

    assert plate.state is PlateState.DISPOSED
    assert plate.location_id == "WASTE_01"
    with pytest.raises(SafetyViolationError, match="disposed"):
        waste.release("P1")
