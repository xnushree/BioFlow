"""Tests for Operation and the operation -> equipment mapping."""

import pytest

from bioflow.domain import EQUIPMENT_FOR_OPERATION, EquipmentKind, Operation


def test_every_operation_has_an_equipment_kind() -> None:
    # Guards against adding a new Operation and forgetting who performs it.
    assert set(EQUIPMENT_FOR_OPERATION) == set(Operation)


def test_transport_is_done_by_robots_and_not_allowed_in_protocols() -> None:
    assert EQUIPMENT_FOR_OPERATION[Operation.TRANSPORT] is EquipmentKind.ROBOT
    assert not Operation.TRANSPORT.allowed_in_protocol


def test_mapping_is_read_only() -> None:
    with pytest.raises(TypeError):
        EQUIPMENT_FOR_OPERATION[Operation.IMAGE] = EquipmentKind.STORAGE  # type: ignore[index]


def test_only_archive_and_dispose_are_terminal() -> None:
    assert {op for op in Operation if op.is_terminal} == {Operation.ARCHIVE, Operation.DISPOSE}


def test_operations_are_plain_strings_for_serialisation() -> None:
    assert Operation("IMAGE") is Operation.IMAGE
    assert f"{Operation.IMAGE}" == "IMAGE"
