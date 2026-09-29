"""Tests for PlateSlots."""

import pytest

from bioflow.core.exceptions import (
    CapacityExceededError,
    DuplicateAllocationError,
    UnknownEntityError,
    ValidationError,
)
from bioflow.equipment.slots import PlateSlots


def test_add_get_remove(make_plate) -> None:
    slots = PlateSlots("INC_01", capacity=2)
    plate = make_plate("P1")

    slots.add(plate)
    assert "P1" in slots
    assert slots.get("P1") is plate
    assert (slots.count, slots.free) == (1, 1)

    assert slots.remove("P1") is plate
    assert "P1" not in slots


def test_capacity_is_enforced(make_plate) -> None:
    slots = PlateSlots("IMG_01", capacity=1)
    slots.add(make_plate("P1"))

    assert slots.is_full
    with pytest.raises(CapacityExceededError) as error:
        slots.add(make_plate("P2"))
    assert error.value.capacity == 1


def test_duplicate_plate_is_rejected(make_plate) -> None:
    slots = PlateSlots("INC_01", capacity=5)
    slots.add(make_plate("P1"))

    with pytest.raises(DuplicateAllocationError):
        slots.add(make_plate("P1"))


def test_unknown_plate_is_reported_with_owner() -> None:
    with pytest.raises(UnknownEntityError, match="plate in INC_01"):
        PlateSlots("INC_01", capacity=1).get("P404")


def test_plate_ids_preserve_insertion_order(make_plate) -> None:
    slots = PlateSlots("S", capacity=3)
    for plate_id in ("P3", "P1", "P2"):
        slots.add(make_plate(plate_id))

    assert slots.plate_ids == ("P3", "P1", "P2")


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        PlateSlots("S", capacity=0)
