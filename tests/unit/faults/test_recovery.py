"""Tests for automatic recovery, each running a small fully wired lab end to end."""

from typing import Any

import pytest

from bioflow.core.events import Event
from bioflow.domain import (
    ContaminationStatus,
    Experiment,
    ExperimentStatus,
    Operation,
    PlateState,
    Protocol,
    ProtocolStep,
)
from bioflow.equipment import EquipmentEvent, Robot, RobotState
from bioflow.equipment.incubator import IncubatorState
from bioflow.equipment.station import StationState
from bioflow.faults.config import FaultConfig, RecoverySettings
from bioflow.faults.fault import FaultSpec, FaultType
from bioflow.faults.recovery import RecoveryEvent
from bioflow.laboratory import Laboratory

INCUBATE_LONG = Protocol("inc", "HEK293", (ProtocolStep(Operation.INCUBATE, 600), ProtocolStep(Operation.ARCHIVE)))
IMAGE_ONLY = Protocol("img", "HEK293", (ProtocolStep(Operation.IMAGE, 30), ProtocolStep(Operation.ARCHIVE)))


def run(lab: Laboratory, protocol: Protocol, plates: int, *faults: FaultSpec,
        submit_at: float = 0.0) -> tuple[Any, list[Event]]:
    log: list[Event] = []
    lab.engine.bus.subscribe("*", log.append)
    lab.schedule_experiment(Experiment("EXP", protocol, plates, submit_at))
    for fault in faults:
        lab.schedule_fault(fault)
    return lab.run(), log


def of_type(log: list[Event], event_type: str) -> list[Event]:
    return [e for e in log if e.event_type == event_type]


def test_incubator_excursion_evacuates_plates_and_keeps_remaining_time(make_lab) -> None:
    lab = make_lab()
    summary, log = run(lab, INCUBATE_LONG, 3,
                       FaultSpec(FaultType.TEMPERATURE_EXCURSION, "INCUBATOR_01", 100, duration_min=200))

    assert summary.tasks_completed == summary.tasks_total
    moved = [e for e in of_type(log, "TASK_REQUEUED") if "evacuating INCUBATOR_01" in e.payload["reason"]]
    assert len(moved) == 3
    for n in (1, 2, 3):
        plate = lab.state.plate(f"EXP-P00{n}")
        assert plate.contamination is ContaminationStatus.SUSPECTED
        task = lab.state.tasks.get(f"EXP-P00{n}-S01")
        assert task.assigned_equipment_id == "INCUBATOR_02"
        assert task.duration_min < 600  # only the incubation time still owed
    assert lab.state.equipment_item("INCUBATOR_01").state is IncubatorState.AVAILABLE  # recovered after repair
    assert summary.recovery is not None and summary.recovery.completed == 1


def test_hung_imaging_run_is_redone_on_another_station(make_lab) -> None:
    lab = make_lab(imaging_stations={"count": 2, "process_time_min": 30})
    summary, log = run(lab, IMAGE_ONLY, 1, FaultSpec(FaultType.IMAGING_FAILURE, "IMAGING_01", 10, duration_min=300))

    assert summary.tasks_completed == summary.tasks_total
    completed = [e for e in of_type(log, EquipmentEvent.PROCESSING_COMPLETED)]
    assert [e.source for e in completed] == ["IMAGING_02"]
    assert not of_type(log, RecoveryEvent.RECOVERY_BLOCKED)  # an alternative existed


def test_only_imager_failing_means_a_safe_hold_until_repair(make_lab) -> None:
    lab = make_lab()
    summary, log = run(lab, IMAGE_ONLY, 1, FaultSpec(FaultType.IMAGING_FAILURE, "IMAGING_01", 10, duration_min=120))

    [blocked] = of_type(log, RecoveryEvent.RECOVERY_BLOCKED)
    assert "no other IMAGING_STATION in service" in blocked.payload["reason"]
    assert summary.tasks_completed == summary.tasks_total  # redone on the same station once repaired
    assert lab.state.equipment_item("IMAGING_01").state in (StationState.IDLE, StationState.OCCUPIED)


@pytest.fixture
def one_incubator_lab(make_lab) -> Laboratory:
    return make_lab(incubators={"count": 1, "capacity": 10})


def test_permanent_failure_with_no_alternative_quarantines_plates(one_incubator_lab: Laboratory) -> None:
    lab = one_incubator_lab
    lab.recovery.settings = RecoverySettings(max_hold_min=60)
    summary, log = run(lab, INCUBATE_LONG, 2, FaultSpec(FaultType.INCUBATOR_FAILURE, "INCUBATOR_01", 100))

    [unrecoverable] = of_type(log, RecoveryEvent.UNRECOVERABLE)
    assert "still unresolved after 60 min" in unrecoverable.payload["reason"]
    assert sorted(unrecoverable.payload["plates"]) == ["EXP-P001", "EXP-P002"]
    for plate in lab.state.plates.values():
        assert plate.state is PlateState.QUARANTINED
    assert lab.state.experiment("EXP").status is ExperimentStatus.FAILED
    assert not summary.stalled  # the safe state is a clean end, not a hang


def test_robot_failure_while_carrying_holds_then_resumes(make_lab) -> None:
    lab = make_lab(robots={"count": 1, "pick_time_min": 0.5, "place_time_min": 0.5}, travel_min=20.0)
    # t=0.5 picked, travelling to the incubator until 20.5; the robot dies at 10.
    summary, log = run(lab, INCUBATE_LONG, 1, FaultSpec(FaultType.ROBOT_FAILURE, "ROBOT_01", 10, duration_min=30))

    [blocked] = of_type(log, RecoveryEvent.RECOVERY_BLOCKED)
    assert "EXP-P001 is on board" in blocked.payload["reason"]
    placed = of_type(log, EquipmentEvent.PLATE_PLACED)[0]
    assert placed.timestamp == pytest.approx(40 + 10.5 + 0.5)  # repaired at 40, 10.5 min of travel left
    assert summary.tasks_completed == summary.tasks_total


def test_robot_failure_before_pickup_hands_the_job_to_another_robot(make_lab) -> None:
    lab = make_lab(travel_min=20.0)
    lab.state.equipment_item("ROBOT_01").location_id = "INCUBATOR_02"  # far from the plates: must travel first
    summary, log = run(lab, INCUBATE_LONG, 1, FaultSpec(FaultType.ROBOT_FAILURE, "ROBOT_01", 5, duration_min=500))

    [aborted] = of_type(log, EquipmentEvent.TRANSPORT_ABORTED)
    assert aborted.source == "ROBOT_01"
    assert of_type(log, EquipmentEvent.PLATE_PICKED)[0].source == "ROBOT_02"
    assert summary.tasks_completed == summary.tasks_total


def test_gripper_failure_hands_the_pick_to_another_robot(make_lab) -> None:
    lab = make_lab()
    summary, log = run(lab, INCUBATE_LONG, 1,
                       FaultSpec(FaultType.PLATE_DETECTION_FAILURE, "ROBOT_01", 0, duration_min=100))

    assert [e.source for e in of_type(log, EquipmentEvent.PICK_FAILED)] == ["ROBOT_01"] * 3
    assert of_type(log, EquipmentEvent.PLATE_PICKED)[0].source == "ROBOT_02"
    robot = lab.state.equipment_item("ROBOT_01")
    assert isinstance(robot, Robot) and robot.state is RobotState.IDLE  # aborted cleanly, not stuck in PICKING
    assert lab.resources.in_service("ROBOT_01")  # back after the maintenance sign-off
    assert summary.tasks_completed == summary.tasks_total


def test_unreachable_incubator_gets_no_new_plates_until_its_link_returns(make_lab) -> None:
    lab = make_lab()
    # The link drops at t=0 and is diagnosed a few minutes later; the plates arrive after that.
    summary, log = run(lab, INCUBATE_LONG, 2,
                       FaultSpec(FaultType.COMMUNICATION_TIMEOUT, "INCUBATOR_01", 0, duration_min=60), submit_at=10)

    placed = {e.payload["destination"] for e in of_type(log, EquipmentEvent.PLATE_PLACED) if e.timestamp < 60}
    assert "INCUBATOR_01" not in placed
    assert lab.resources.in_service("INCUBATOR_01")
    assert summary.tasks_completed == summary.tasks_total


def test_robot_progress_during_supervisory_fault_is_remembered(make_lab) -> None:
    lab = make_lab()
    robot = lab.state.equipment_item("ROBOT_01")
    assert isinstance(robot, Robot)
    robot.enter_fault()

    robot._set_phase_state(RobotState.IDLE)  # physical progress while held in FAULT

    assert robot.state is RobotState.FAULT
    robot.begin_recovery()
    robot.complete_recovery()
    assert robot.state is RobotState.IDLE


def test_fault_config_recovery_section() -> None:
    assert FaultConfig().recovery.max_hold_min == 240.0
