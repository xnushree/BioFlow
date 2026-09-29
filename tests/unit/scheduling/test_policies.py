"""Tests for the Priority, Deadline (EDF) and Cost schedulers, and scheduling config."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from bioflow.core.exceptions import ConfigurationError
from bioflow.domain import Experiment, Operation, Protocol, ProtocolStep
from bioflow.laboratory import Laboratory
from bioflow.protocols.task_builder import build_tasks
from bioflow.scheduling.base_scheduler import SchedulingView
from bioflow.scheduling.config import CostSettings, CostWeights, load_scheduling_config, parse_scheduling_config
from bioflow.scheduling.cost_scheduler import CostBreakdown, CostScheduler
from bioflow.scheduling.deadline_scheduler import DeadlineScheduler
from bioflow.scheduling.priority_scheduler import PriorityScheduler
from bioflow.scheduling.registry import SCHEDULERS, create_scheduler

REPO_CONFIG = Path(__file__).parents[3] / "configs" / "scheduling.yaml"
IMAGE_THEN_ARCHIVE = Protocol("img", "HEK293", (ProtocolStep(Operation.IMAGE, 10), ProtocolStep(Operation.ARCHIVE)))
INCUBATE_THEN_ARCHIVE = Protocol("inc", "CHO", (ProtocolStep(Operation.INCUBATE, 60), ProtocolStep(Operation.ARCHIVE)))


@dataclass(frozen=True)
class TableTravel:
    """Travel times from a table; unlisted trips take ``default`` minutes."""

    table: dict[tuple[str, str], float]
    default: float = 5.0

    def travel_time(self, from_id: str, to_id: str) -> float:
        if from_id == to_id:
            return 0.0
        return self.table.get((from_id, to_id), self.table.get((to_id, from_id), self.default))


def submit(lab: Laboratory, exp_id: str, protocol: Protocol, priority: int = 0,
           deadline: float | None = None, plates: int = 1) -> None:
    """Register an experiment without dispatching, so ready tasks can be inspected."""
    experiment = Experiment(exp_id, protocol, plates, 0.0, priority=priority, deadline=deadline)
    new_plates = experiment.create_plates()
    lab.state.register_experiment(experiment, new_plates)
    lab.state.tasks.add_tasks(build_tasks(experiment), now=0.0)
    for plate in new_plates:
        lab.state.equipment_item("STORAGE_01").receive(plate)  # type: ignore[attr-defined]


def view_of(lab: Laboratory, now: float = 0.0, travel=None) -> SchedulingView:
    return SchedulingView(
        now=now, experiments=lab.state.experiments, plates=lab.state.plates,
        equipment=lab.state.equipment, resources=lab.resources, travel=travel or TableTravel({}),
    )


def ready_ids(tasks) -> list[str]:
    return [t.task_id for t in tasks]


# ----------------------------------------------------------- priority / EDF
def test_priority_orders_high_first_and_keeps_fifo_within_a_level(make_lab) -> None:
    lab = make_lab()
    submit(lab, "LOW", IMAGE_THEN_ARCHIVE, priority=1, plates=2)
    submit(lab, "HIGH", IMAGE_THEN_ARCHIVE, priority=5, plates=2)

    ordered = PriorityScheduler().order(lab.state.tasks.ready_tasks(), view_of(lab))

    assert ready_ids(ordered) == ["HIGH-P001-S01", "HIGH-P002-S01", "LOW-P001-S01", "LOW-P002-S01"]


def test_edf_orders_by_deadline_with_no_deadline_last(make_lab) -> None:
    lab = make_lab()
    submit(lab, "NONE", IMAGE_THEN_ARCHIVE)
    submit(lab, "LATE", IMAGE_THEN_ARCHIVE, deadline=900)
    submit(lab, "SOON", IMAGE_THEN_ARCHIVE, deadline=100)

    ordered = DeadlineScheduler().order(lab.state.tasks.ready_tasks(), view_of(lab))

    assert ready_ids(ordered) == ["SOON-P001-S01", "LATE-P001-S01", "NONE-P001-S01"]


# ---------------------------------------------------------------- cost terms
def test_waiting_time_term(make_lab) -> None:
    lab = make_lab()
    submit(lab, "E", IMAGE_THEN_ARCHIVE)
    task = lab.state.tasks.get("E-P001-S01")

    breakdown = CostScheduler().explain(task, view_of(lab, now=30.0))

    assert breakdown is not None and breakdown.waited_min == 30.0


def test_critical_ratio_uses_remaining_protocol_work(make_lab) -> None:
    lab = make_lab()
    submit(lab, "E", IMAGE_THEN_ARCHIVE, deadline=1000)
    task = lab.state.tasks.get("E-P001-S01")
    scheduler = CostScheduler(CostSettings(default_step_min=15))

    # remaining = IMAGE 10 + ARCHIVE (no duration -> default 15) = 25; time left = 1000 - 500
    assert scheduler.explain(task, view_of(lab, now=500)).critical_ratio == pytest.approx(25 / 500)
    assert scheduler.explain(task, view_of(lab, now=1200)).critical_ratio == 10.0  # past deadline: capped


def test_no_deadline_means_no_deadline_pressure(make_lab) -> None:
    lab = make_lab()
    submit(lab, "E", IMAGE_THEN_ARCHIVE)

    assert CostScheduler().explain(lab.state.tasks.get("E-P001-S01"), view_of(lab)).critical_ratio == 0.0


def test_nearest_robot_is_chosen(make_lab) -> None:
    lab = make_lab()
    submit(lab, "E", IMAGE_THEN_ARCHIVE)
    lab.state.equipment_item("ROBOT_01").location_id = "WASTE_01"  # type: ignore[attr-defined]
    lab.state.equipment_item("ROBOT_02").location_id = "INCUBATOR_01"  # type: ignore[attr-defined]
    travel = TableTravel({("INCUBATOR_01", "STORAGE_01"): 1.0, ("WASTE_01", "STORAGE_01"): 9.0})

    chosen = CostScheduler().choose_robot(lab.state.tasks.get("E-P001-S01"), ["ROBOT_01", "ROBOT_02"],
                                          view_of(lab, travel=travel))

    assert chosen == "ROBOT_02"


def test_switching_cost_applies_to_stations_after_a_cell_type_change(make_lab) -> None:
    lab = make_lab(imaging_stations={"count": 2, "process_time_min": 10})
    submit(lab, "CHO_EXP", Protocol("c", "CHO", IMAGE_THEN_ARCHIVE.steps))
    submit(lab, "HEK_EXP", IMAGE_THEN_ARCHIVE)
    scheduler = CostScheduler(CostSettings(weights=CostWeights(travel=0, switching=5, delay=0, idle=0, deadline=0)))
    view = view_of(lab)

    # IMAGING_01 last handled CHO, so a HEK293 plate should go to the untouched IMAGING_02.
    scheduler.choose_destination(lab.state.tasks.get("CHO_EXP-P001-S01"), ["IMAGING_01"], view)
    hek_task = lab.state.tasks.get("HEK_EXP-P001-S01")

    assert scheduler.choose_destination(hek_task, ["IMAGING_01", "IMAGING_02"], view) == "IMAGING_02"


def test_idle_destinations_are_preferred(make_lab) -> None:
    lab = make_lab()
    submit(lab, "E", INCUBATE_THEN_ARCHIVE, plates=2)
    scheduler = CostScheduler(CostSettings(weights=CostWeights(travel=0, switching=0, delay=0, idle=1, deadline=0)))
    first, second = lab.state.tasks.get("E-P001-S01"), lab.state.tasks.get("E-P002-S01")

    assert scheduler.choose_destination(first, ["INCUBATOR_01", "INCUBATOR_02"], view_of(lab, now=100)) == "INCUBATOR_01"
    assert scheduler.choose_destination(second, ["INCUBATOR_01", "INCUBATOR_02"], view_of(lab, now=100)) == "INCUBATOR_02"


def test_urgent_task_is_ordered_first(make_lab) -> None:
    lab = make_lab()
    submit(lab, "RELAXED", IMAGE_THEN_ARCHIVE, deadline=10_000)
    submit(lab, "URGENT", IMAGE_THEN_ARCHIVE, deadline=40)

    ordered = CostScheduler().order(lab.state.tasks.ready_tasks(), view_of(lab))

    assert ready_ids(ordered)[0] == "URGENT-P001-S01"


def test_infeasible_tasks_are_ordered_last(make_lab) -> None:
    lab = make_lab()
    submit(lab, "IMG", IMAGE_THEN_ARCHIVE, deadline=10)  # very urgent but the imager is reserved
    submit(lab, "INC", INCUBATE_THEN_ARCHIVE)
    lab.resources.reserve("IMAGING_01", "SOMEONE_ELSE")
    scheduler = CostScheduler()

    ordered = scheduler.order(lab.state.tasks.ready_tasks(), view_of(lab))

    assert ready_ids(ordered) == ["INC-P001-S01", "IMG-P001-S01"]
    assert scheduler.explain(lab.state.tasks.get("IMG-P001-S01"), view_of(lab)) is None


def test_breakdown_total_applies_weights_and_signs() -> None:
    b = CostBreakdown(travel_min=4, switch=1, waited_min=100, dest_idle_min=50, critical_ratio=0.5)
    w = CostWeights(travel=1, switching=5, delay=0.1, idle=0.2, deadline=10)

    assert b.total(CostSettings(weights=w)) == pytest.approx(4 + 5 - 10 - 10 - 5)


# ------------------------------------------------------------ config/registry
def test_repository_scheduling_config_loads() -> None:
    config = load_scheduling_config(REPO_CONFIG)

    assert config.cost.weights == CostWeights()  # the file and the code defaults agree


def test_empty_config_uses_defaults() -> None:
    assert parse_scheduling_config(None).cost == CostSettings()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"cots": {}}, "unknown key 'cots' \\(did you mean 'cost'"),
        ({"cost": {"weights": {"travle": 1}}}, "did you mean 'travel'"),
        ({"cost": {"weights": {"travel": -1}}}, "weight 'travel' must be >= 0"),
        ({"cost": {"weights": {"travel": "far"}}}, "expected a number"),
        ({"cost": {"default_step_min": 0}}, "default_step_min must be positive"),
    ],
)
def test_invalid_scheduling_config(data: dict, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        parse_scheduling_config(data)


def test_registry_builds_every_policy_as_fresh_instances() -> None:
    assert set(SCHEDULERS) == {"fifo", "priority", "deadline", "cost"}
    assert create_scheduler("cost") is not create_scheduler("cost")
