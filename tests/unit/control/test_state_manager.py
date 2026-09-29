"""Tests for StateManager, FifoScheduler and the scheduler registry."""

import pytest

from bioflow.control.state_manager import StateManager
from bioflow.core.exceptions import ConfigurationError, UnknownEntityError, ValidationError
from bioflow.domain import Experiment, Operation, Protocol, ProtocolStep, Task
from bioflow.robotics.travel import ConstantTravelTime
from bioflow.scheduling.base_scheduler import SchedulingView
from bioflow.scheduling.fifo_scheduler import FifoScheduler
from bioflow.scheduling.registry import create_scheduler

PROTOCOL = Protocol("p", "HEK293", (ProtocolStep(Operation.IMAGE), ProtocolStep(Operation.ARCHIVE)))


def test_register_and_look_up() -> None:
    state = StateManager({})
    exp = Experiment("EXP001", PROTOCOL, 2, 0.0)
    plates = exp.create_plates()

    state.register_experiment(exp, plates)

    assert state.experiment("EXP001") is exp
    assert state.plate("EXP001-P002") is plates[1]
    with pytest.raises(UnknownEntityError, match="plate"):
        state.plate("NOPE")


def test_duplicate_experiment_is_rejected_without_side_effects() -> None:
    state = StateManager({})
    exp = Experiment("EXP001", PROTOCOL, 1, 0.0)
    state.register_experiment(exp, exp.create_plates())

    with pytest.raises(ValidationError, match="already exists"):
        state.register_experiment(Experiment("EXP001", PROTOCOL, 1, 0.0), [])


def test_registries_are_read_only() -> None:
    with pytest.raises(TypeError):
        StateManager({}).plates["X"] = None  # type: ignore[index]


def test_fifo_keeps_ready_order_and_picks_first_candidates() -> None:
    tasks = [Task(f"T{i}", "E", "P", Operation.IMAGE) for i in (3, 1, 2)]
    view = SchedulingView(0.0, {}, {}, {}, None, ConstantTravelTime(1.0))  # type: ignore[arg-type]
    fifo = FifoScheduler()

    assert fifo.order(tasks, view) == tasks
    assert fifo.choose_destination(tasks[0], ["IMAGING_01", "IMAGING_02"], view) == "IMAGING_01"
    assert fifo.choose_robot(tasks[0], ["ROBOT_02"], view) == "ROBOT_02"


def test_registry_creates_by_name_and_suggests_on_typo() -> None:
    assert isinstance(create_scheduler("fifo"), FifoScheduler)
    with pytest.raises(ConfigurationError, match="did you mean 'fifo'"):
        create_scheduler("fifoo")


def test_constant_travel_time() -> None:
    travel = ConstantTravelTime(2.5)

    assert travel.travel_time("A", "B") == 2.5
    assert travel.travel_time("A", "A") == 0.0
    with pytest.raises(ValidationError):
        ConstantTravelTime(-1)
