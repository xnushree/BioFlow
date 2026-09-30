"""Tests for Storage and WasteStation."""


from bioflow.core.events import Event
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


def test_archived_plates_leave_the_lab_and_free_their_slot(
    engine: SimulationEngine, make_plate, event_log: list[Event]
) -> None:
    storage = Storage("STORAGE_01", engine, ContainerSpec(capacity=1))
    plate = make_plate("P1")
    storage.receive(plate)
    assert storage.state is StorageState.FULL

    storage.archive("P1")

    assert plate.state is PlateState.ARCHIVED and plate.is_finished
    assert plate.location_id == "OFFSITE_ARCHIVE"
    assert storage.occupancy == 0 and storage.state is StorageState.AVAILABLE
    lifecycle = [e.event_type for e in event_log if e.event_type != EquipmentEvent.STATE_CHANGED]
    assert lifecycle[-2:] == [EquipmentEvent.PLATE_ARCHIVED, EquipmentEvent.PLATE_RELEASED]
    storage.receive(make_plate("P2"))  # the freed slot is usable again


def test_disposed_plates_leave_the_waste_station(engine: SimulationEngine, make_plate) -> None:
    waste = WasteStation("WASTE_01", engine, ContainerSpec(capacity=1))

    for plate_id in ("P1", "P2", "P3"):  # more plates than the drop-off buffer holds
        plate = make_plate(plate_id)
        waste.receive(plate)
        assert plate.state is PlateState.DISPOSED and plate.location_id == "DISPOSED"

    assert waste.occupancy == 0
