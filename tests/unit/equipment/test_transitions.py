"""Exhaustive transition tests for equipment, and enforcement through _set_state."""

import pytest

from bioflow.core.events import Event
from bioflow.core.exceptions import InvalidTransitionError
from bioflow.core.simulation import SimulationEngine
from bioflow.equipment import EquipmentEvent, Robot, RobotState
from bioflow.equipment.config import RobotSpec
from bioflow.equipment.incubator import INCUBATOR_TRANSITIONS
from bioflow.equipment.robot import ROBOT_TRANSITIONS
from bioflow.equipment.station import STATION_TRANSITIONS
from bioflow.equipment.storage import STORAGE_TRANSITIONS


def to_fault(states: list[str], fault: str = "FAULT") -> set[tuple[str, str]]:
    """'ANY STATE -> FAULT' edges for the given states."""
    return {(state, fault) for state in states if state != fault}


def test_robot_transitions(assert_exact_transitions) -> None:
    normal_cycle = {
        ("IDLE", "ASSIGNED"),
        ("ASSIGNED", "MOVING"), ("ASSIGNED", "IDLE"),
        ("MOVING", "PICKING"),
        ("PICKING", "TRANSPORTING"),
        ("TRANSPORTING", "PLACING"),
        ("PLACING", "IDLE"),
    }
    safety = {
        ("MOVING", "SAFE_STOP"), ("TRANSPORTING", "SAFE_STOP"),
        ("SAFE_STOP", "RECOVERY"), ("FAULT", "RECOVERY"), ("RECOVERY", "IDLE"),
    }
    assert_exact_transitions(ROBOT_TRANSITIONS, normal_cycle | safety | to_fault([s.value for s in RobotState]))


def test_incubator_transitions(assert_exact_transitions) -> None:
    states = ["AVAILABLE", "FULL", "ENVIRONMENTAL_FAULT", "FAULT", "RECOVERY"]
    expected = {
        ("AVAILABLE", "FULL"), ("FULL", "AVAILABLE"),
        ("AVAILABLE", "ENVIRONMENTAL_FAULT"), ("FULL", "ENVIRONMENTAL_FAULT"),
        ("ENVIRONMENTAL_FAULT", "RECOVERY"), ("FAULT", "RECOVERY"),
        ("RECOVERY", "AVAILABLE"), ("RECOVERY", "FULL"),
    } | to_fault(states)
    assert_exact_transitions(INCUBATOR_TRANSITIONS, expected)


def test_station_transitions(assert_exact_transitions) -> None:
    states = ["IDLE", "OCCUPIED", "PROCESSING", "FAULT", "RECOVERY"]
    expected = {
        ("IDLE", "OCCUPIED"), ("OCCUPIED", "IDLE"),
        ("OCCUPIED", "PROCESSING"), ("PROCESSING", "OCCUPIED"),
        ("FAULT", "RECOVERY"), ("RECOVERY", "IDLE"), ("RECOVERY", "OCCUPIED"),
    } | to_fault(states)
    assert_exact_transitions(STATION_TRANSITIONS, expected)


def test_storage_transitions(assert_exact_transitions) -> None:
    assert_exact_transitions(STORAGE_TRANSITIONS, {("AVAILABLE", "FULL"), ("FULL", "AVAILABLE")})


def test_fault_recovery_never_skips_recovery() -> None:
    for table, fault in ((ROBOT_TRANSITIONS, RobotState.FAULT), (INCUBATOR_TRANSITIONS, "FAULT"),
                         (STATION_TRANSITIONS, "FAULT")):
        assert {str(s) for s in table.targets(table.state_type(fault))} == {"RECOVERY"}


def test_equipment_rejects_illegal_state_change_without_publishing(
    engine: SimulationEngine, event_log: list[Event]
) -> None:
    robot = Robot("ROBOT_01", engine, RobotSpec(0.5, 0.5), home_location_id="STORAGE_01")

    with pytest.raises(InvalidTransitionError, match="ROBOT_01: invalid transition IDLE -> PLACING"):
        robot._set_state(RobotState.PLACING)

    assert robot.state is RobotState.IDLE
    assert not [e for e in event_log if e.event_type == EquipmentEvent.STATE_CHANGED]
