"""Tests for the Dispatcher, driven through a small fully wired Laboratory."""

import pytest

from bioflow.control.dispatcher import DispatchEvent
from bioflow.core.events import ALL_EVENTS, Event
from bioflow.core.exceptions import CapacityExceededError
from bioflow.domain import Experiment, ExperimentStatus, Operation, PlateState, Protocol, ProtocolStep, TaskStatus

BASIC = Protocol("basic", "HEK293", (
    ProtocolStep(Operation.INCUBATE, 720),
    ProtocolStep(Operation.MEDIA_EXCHANGE),
    ProtocolStep(Operation.INCUBATE, 1440),
    ProtocolStep(Operation.IMAGE),
    ProtocolStep(Operation.ARCHIVE),
))


def experiment(protocol: Protocol = BASIC, plates: int = 1, exp_id: str = "EXP001", at: float = 0.0) -> Experiment:
    return Experiment(exp_id, protocol, plate_count=plates, submitted_at=at)


def record(lab) -> list[Event]:
    log: list[Event] = []
    lab.engine.bus.subscribe(ALL_EVENTS, log.append)
    return log


def test_single_plate_follows_protocol_with_exact_timing(make_lab) -> None:
    lab = make_lab(travel_min=2.0)
    lab.schedule_experiment(experiment())

    summary = lab.run()

    # Each transport: 2 travel + 0.5 pick + 0.5 place (the robot is already at the source).
    # 3 + 720 | 3 + 15 | 3 + 1440 | 3 + 10 | 3 (archive)
    assert summary.makespan == pytest.approx(2200.0)
    assert summary.tasks_completed == summary.tasks_total == 5
    plate = lab.state.plate("EXP001-P001")
    assert (plate.state, plate.location_id) == (PlateState.ARCHIVED, "OFFSITE_ARCHIVE")
    assert lab.state.experiment("EXP001").status is ExperimentStatus.COMPLETED


def test_task_lifecycle_events_are_published_in_order(make_lab) -> None:
    lab = make_lab()
    log = record(lab)
    lab.schedule_experiment(experiment())

    lab.run()

    lifecycle = [(e.event_type, e.payload.get("operation")) for e in log if e.source == "DISPATCHER"]
    assert lifecycle[0] == (DispatchEvent.EXPERIMENT_SUBMITTED, None)
    assert lifecycle[1:3] == [(DispatchEvent.TASK_DISPATCHED, Operation.INCUBATE),
                              (DispatchEvent.TASK_COMPLETED, Operation.INCUBATE)]
    assert lifecycle[-1] == (DispatchEvent.EXPERIMENT_FINISHED, None)


def test_tasks_never_start_before_dependencies_finish(make_lab) -> None:
    lab = make_lab()
    lab.schedule_experiment(experiment(plates=6))

    lab.run()

    for task in lab.state.tasks:
        for dep in task.depends_on:
            assert task.started_at >= lab.state.tasks.get(dep).completed_at


def test_single_imaging_station_is_never_double_booked(make_lab) -> None:
    lab = make_lab()
    log = record(lab)
    lab.schedule_experiment(experiment(plates=5))

    lab.run()

    occupancy, peak = 0, 0
    for e in log:
        if e.source == "IMAGING_01" and e.event_type in ("PLATE_RECEIVED", "PLATE_RELEASED"):
            occupancy += 1 if e.event_type == "PLATE_RECEIVED" else -1
            peak = max(peak, occupancy)
    assert peak == 1


def test_blocked_task_does_not_block_others(make_lab) -> None:
    """A plate waiting for the busy imager must not stop a later plate that needs an incubator."""
    image_first = Protocol("img", "HEK293", (ProtocolStep(Operation.IMAGE, 100), ProtocolStep(Operation.ARCHIVE)))
    incubate_first = Protocol("inc", "HEK293", (ProtocolStep(Operation.INCUBATE, 5), ProtocolStep(Operation.ARCHIVE)))
    lab = make_lab()
    lab.schedule_experiment(experiment(image_first, plates=2, exp_id="IMG"))
    lab.schedule_experiment(experiment(incubate_first, plates=1, exp_id="INC"))

    lab.run(until=10)

    # IMG-P002 is ahead in FIFO order but the imager is taken by IMG-P001; INC still ran.
    assert lab.state.tasks.get("IMG-P002-S01").status is TaskStatus.READY
    assert lab.state.tasks.get("INC-P001-S01").status is not TaskStatus.READY


def test_consecutive_steps_on_same_equipment_kind_run_in_place(make_lab) -> None:
    double = Protocol("double", "HEK293", (
        ProtocolStep(Operation.INCUBATE, 60), ProtocolStep(Operation.INCUBATE, 60), ProtocolStep(Operation.ARCHIVE),
    ))
    lab = make_lab()
    log = record(lab)
    lab.schedule_experiment(experiment(double))

    summary = lab.run()

    transports = [e for e in log if e.event_type == "TRANSPORT_STARTED"]
    assert len(transports) == 2  # storage -> incubator, incubator -> storage
    assert summary.makespan == pytest.approx(3 + 60 + 60 + 3)


def test_dispose_ends_at_waste_station(make_lab) -> None:
    to_waste = Protocol("w", "HEK293", (ProtocolStep(Operation.IMAGE), ProtocolStep(Operation.DISPOSE)))
    lab = make_lab()
    lab.schedule_experiment(experiment(to_waste))

    lab.run()

    plate = lab.state.plate("EXP001-P001")
    assert (plate.state, plate.location_id) == (PlateState.DISPOSED, "DISPOSED")
    assert lab.resources.status("WASTE_01").occupancy == 0


def test_experiment_waits_until_its_submission_time(make_lab) -> None:
    lab = make_lab()
    lab.schedule_experiment(experiment(at=500.0))

    lab.run(until=499)
    assert "EXP001" not in lab.state.experiments

    lab.run(until=501)
    assert lab.state.experiment("EXP001").status is ExperimentStatus.RUNNING


def test_submission_rejected_atomically_when_storage_is_too_small(make_lab) -> None:
    lab = make_lab(storage={"count": 1, "capacity": 3})

    with pytest.raises(CapacityExceededError):
        lab.dispatcher.submit(experiment(plates=4))

    assert "EXP001" not in lab.state.experiments
    assert len(lab.state.tasks) == 0
    assert lab.resources.status("STORAGE_01").occupancy == 0


def test_lab_is_clean_after_run(make_lab) -> None:
    lab = make_lab()
    lab.schedule_experiment(experiment(plates=8))

    lab.run()

    assert all(r["reserved"] == 0 for r in lab.resources.snapshot())
    assert lab.resources.available_robots() == ["ROBOT_01", "ROBOT_02"]
    assert all(p.is_finished for p in lab.state.plates.values())
    assert lab.engine.pending_count == 0
