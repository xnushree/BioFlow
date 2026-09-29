"""Tests for ResourceManager."""

from typing import Any

import pytest

from bioflow.control.resource_manager import ResourceEvent, ResourceManager
from bioflow.core.events import Event
from bioflow.core.exceptions import (
    CapacityExceededError,
    DuplicateAllocationError,
    ResourceUnavailableError,
    SafetyViolationError,
    UnknownEntityError,
)
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import EquipmentKind, Operation, PlateState
from bioflow.equipment import Incubator, ProcessingStation, Robot, Storage, TransportJob
from bioflow.equipment.config import ContainerSpec, IncubatorSpec, RobotSpec, StationSpec
from bioflow.equipment.station import StationState
from bioflow.robotics.motion import TimedMotion
from bioflow.robotics.travel import ConstantTravelTime


@pytest.fixture
def lab(engine: SimulationEngine) -> dict[str, Any]:
    return {
        "STORAGE_01": Storage("STORAGE_01", engine, ContainerSpec(capacity=10)),
        "INCUBATOR_01": Incubator("INCUBATOR_01", engine, IncubatorSpec(capacity=2)),
        "INCUBATOR_02": Incubator("INCUBATOR_02", engine, IncubatorSpec(capacity=2)),
        "IMAGING_01": ProcessingStation("IMAGING_01", engine, Operation.IMAGE, StationSpec(10)),
        "ROBOT_01": Robot("ROBOT_01", engine, RobotSpec(0.5, 0.5), "STORAGE_01",
                          motion=TimedMotion(engine, ConstantTravelTime(1.0))),
    }


@pytest.fixture
def manager(lab: dict[str, Any], engine: SimulationEngine) -> ResourceManager:
    return ResourceManager(lab, engine, engine.bus)


def events_of(log: list[Event], event_type: str) -> list[Event]:
    return [e for e in log if e.event_type == event_type]


def test_initial_status(manager: ResourceManager) -> None:
    status = manager.status("INCUBATOR_01")

    assert (status.capacity, status.occupancy, status.reserved, status.free) == (2, 0, 0, 2)
    assert status.available


def test_reservation_counts_against_capacity(manager: ResourceManager, event_log: list[Event]) -> None:
    reservation = manager.reserve("INCUBATOR_01", "P1", task_id="T1")

    assert manager.status("INCUBATOR_01").free == 1
    assert manager.reservation_for_plate("P1") == reservation
    assert reservation.reservation_id == "RSV000001"
    assert events_of(event_log, ResourceEvent.RESERVED)[0].payload["task_id"] == "T1"


def test_occupancy_and_reservations_together_limit_capacity(
    manager: ResourceManager, lab: dict[str, Any], make_plate
) -> None:
    lab["INCUBATOR_01"].receive(make_plate("P1"))  # physically present
    manager.reserve("INCUBATOR_01", "P2")  # on its way

    with pytest.raises(CapacityExceededError):
        manager.reserve("INCUBATOR_01", "P3")


def test_single_plate_station_accepts_one_reservation(manager: ResourceManager) -> None:
    manager.reserve("IMAGING_01", "P1")

    with pytest.raises(CapacityExceededError):
        manager.reserve("IMAGING_01", "P2")
    assert manager.available(EquipmentKind.IMAGING_STATION) == []


def test_available_lists_only_containers_with_free_capacity(manager: ResourceManager) -> None:
    manager.reserve("INCUBATOR_01", "P1")
    manager.reserve("INCUBATOR_01", "P2")

    assert manager.available(EquipmentKind.INCUBATOR) == ["INCUBATOR_02"]


def test_reservations_at_lists_incoming_plates(manager: ResourceManager) -> None:
    manager.reserve("INCUBATOR_01", "P1")
    manager.reserve("INCUBATOR_01", "P2")
    manager.reserve("INCUBATOR_02", "P3")

    assert [r.plate_id for r in manager.reservations_at("INCUBATOR_01")] == ["P1", "P2"]
    with pytest.raises(UnknownEntityError):
        manager.reservations_at("NOPE_99")


def test_plate_can_hold_only_one_reservation(manager: ResourceManager) -> None:
    manager.reserve("INCUBATOR_01", "P1")

    with pytest.raises(DuplicateAllocationError):
        manager.reserve("INCUBATOR_02", "P1")


def test_cannot_reserve_where_plate_already_is(manager: ResourceManager, lab: dict[str, Any], make_plate) -> None:
    lab["INCUBATOR_01"].receive(make_plate("P1"))

    with pytest.raises(DuplicateAllocationError):
        manager.reserve("INCUBATOR_01", "P1")


@pytest.mark.parametrize("resource_id", ["NOPE_99", "ROBOT_01"])
def test_unknown_or_non_container_resource(manager: ResourceManager, resource_id: str) -> None:
    with pytest.raises(UnknownEntityError, match="plate-holding resource"):
        manager.reserve(resource_id, "P1")


def test_faulted_equipment_is_unavailable(manager: ResourceManager, lab: dict[str, Any]) -> None:
    lab["IMAGING_01"]._set_state(StationState.FAULT)  # public fault API arrives in Phase 15

    assert not manager.status("IMAGING_01").available
    assert manager.available(EquipmentKind.IMAGING_STATION) == []
    with pytest.raises(ResourceUnavailableError, match="not operational"):
        manager.reserve("IMAGING_01", "P1")


def test_cancel_frees_capacity_and_announces_it(manager: ResourceManager, event_log: list[Event]) -> None:
    reservation = manager.reserve("IMAGING_01", "P1")

    assert manager.cancel(reservation.reservation_id) is True
    assert manager.cancel(reservation.reservation_id) is False
    assert manager.status("IMAGING_01").free == 1
    assert manager.reservation_for_plate("P1") is None
    assert len(events_of(event_log, ResourceEvent.RESERVATION_CANCELLED)) == 1
    assert events_of(event_log, ResourceEvent.AVAILABLE)[-1].payload["resource_id"] == "IMAGING_01"


def test_arrival_fulfils_reservation_without_changing_free_capacity(
    engine: SimulationEngine, manager: ResourceManager, lab: dict[str, Any], make_plate, event_log: list[Event]
) -> None:
    lab["STORAGE_01"].receive(make_plate("P1"))
    manager.reserve("INCUBATOR_01", "P1")
    free_before = manager.status("INCUBATOR_01").free

    lab["ROBOT_01"].start_transport(TransportJob("P1", lab["STORAGE_01"], lab["INCUBATOR_01"]))
    engine.run()

    status = manager.status("INCUBATOR_01")
    assert (status.occupancy, status.reserved, status.free) == (1, 0, free_before)
    assert manager.reservation_for_plate("P1") is None
    assert len(events_of(event_log, ResourceEvent.RESERVATION_FULFILLED)) == 1


def test_arrival_at_wrong_destination_is_a_safety_violation(
    manager: ResourceManager, lab: dict[str, Any], make_plate
) -> None:
    manager.reserve("INCUBATOR_01", "P1")

    with pytest.raises(SafetyViolationError, match="reserved for INCUBATOR_01"):
        lab["INCUBATOR_02"].receive(make_plate("P1"))


def test_unreserved_arrival_cannot_steal_reserved_capacity(
    manager: ResourceManager, lab: dict[str, Any], make_plate
) -> None:
    manager.reserve("IMAGING_01", "P1")

    with pytest.raises(SafetyViolationError, match="unreserved arrival of P2"):
        lab["IMAGING_01"].receive(make_plate("P2"))


def test_unreserved_arrival_with_spare_capacity_is_allowed(
    manager: ResourceManager, lab: dict[str, Any], make_plate
) -> None:
    lab["STORAGE_01"].receive(make_plate("P1", state=PlateState.CREATED))  # initial loading

    assert manager.status("STORAGE_01").occupancy == 1


def test_release_announces_available(manager: ResourceManager, lab: dict[str, Any], make_plate,
                                     event_log: list[Event]) -> None:
    lab["IMAGING_01"].receive(make_plate("P1"))
    lab["IMAGING_01"].release("P1")

    available = events_of(event_log, ResourceEvent.AVAILABLE)
    assert [e.payload["resource_id"] for e in available] == ["IMAGING_01"]


def test_robot_availability_follows_transport(
    engine: SimulationEngine, manager: ResourceManager, lab: dict[str, Any], make_plate, event_log: list[Event]
) -> None:
    lab["STORAGE_01"].receive(make_plate("P1"))
    manager.reserve("INCUBATOR_01", "P1")
    lab["ROBOT_01"].start_transport(TransportJob("P1", lab["STORAGE_01"], lab["INCUBATOR_01"]))

    assert manager.available_robots() == []
    engine.run()

    assert manager.available_robots() == ["ROBOT_01"]
    assert events_of(event_log, ResourceEvent.AVAILABLE)[-1].payload["resource_id"] == "ROBOT_01"


def test_snapshot_lists_every_container(manager: ResourceManager) -> None:
    manager.reserve("INCUBATOR_01", "P1")

    rows = {row["resource_id"]: row for row in manager.snapshot()}

    assert set(rows) == {"STORAGE_01", "INCUBATOR_01", "INCUBATOR_02", "IMAGING_01"}
    assert rows["INCUBATOR_01"]["reserved"] == 1 and rows["INCUBATOR_01"]["free"] == 1
