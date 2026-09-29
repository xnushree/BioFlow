"""Tests for workload generation, benchmark statistics, and deferred arrivals."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from bioflow.analytics.performance import aggregate, load_benchmark_plan, paired_comparison, run_one
from bioflow.core.exceptions import ConfigurationError, ValidationError
from bioflow.domain import Experiment, Operation, Protocol, ProtocolStep
from bioflow.equipment.config import load_equipment_config
from bioflow.faults.fault import VALID_TARGETS, FaultSpec, FaultType
from bioflow.laboratory import DEFERRED_EVENT
from bioflow.protocols import load_protocol_library
from bioflow.scenario import parse_scenario
from bioflow.workload import FaultPlan, WorkloadSpec, equipment_ids, generate_scenario, nominal_duration, \
    scenario_to_yaml

ROOT = Path(__file__).parents[3]


@pytest.fixture(autouse=True)
def run_from_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)


def spec(**overrides: Any) -> WorkloadSpec:
    fields: dict[str, Any] = dict(
        name="T", plates=30, experiment_plates=(2, 6), arrival_window_min=300,
        protocol_mix={"basic_experiment": 1, "stress_test": 1}, equipment_config=Path("configs/equipment.yaml"),
        protocol_dir=Path("protocols"), priority_range=(1, 4), deadline_slack=(1.2, 1.5),
        equipment_overrides={"robots": {"count": 3}, "imaging_stations": {"count": 2}},
        laboratory_config=Path("configs/laboratory.yaml"),
        faults=FaultPlan(4, (FaultType.ROBOT_FAILURE, FaultType.IMAGING_FAILURE, FaultType.INCUBATOR_FAILURE),
                         (100, 200), (30, 60)),
    )
    fields.update(overrides)
    return WorkloadSpec(**fields)


# ------------------------------------------------------------- generation
def test_same_seed_same_scenario_different_seed_different_scenario() -> None:
    assert generate_scenario(spec(), 3) == generate_scenario(spec(), 3)
    assert generate_scenario(spec(), 3).experiments != generate_scenario(spec(), 4).experiments


def test_generated_experiments_follow_the_recipe() -> None:
    scenario = generate_scenario(spec(), 1)
    experiments = scenario.experiments

    assert sum(e.plates for e in experiments) == 30
    assert all(1 <= e.plates <= 6 for e in experiments)
    assert all(1 <= e.priority <= 4 for e in experiments)
    assert all(0 <= e.submit_at_min <= 300 for e in experiments)
    assert [e.submit_at_min for e in experiments] == sorted(e.submit_at_min for e in experiments)
    assert scenario.name == "T-seed1" and scenario.seed == 1


def test_deadlines_are_relative_to_nominal_duration() -> None:
    equipment = load_equipment_config(Path("configs/equipment.yaml"))
    protocols = load_protocol_library(Path("protocols"))
    for experiment in generate_scenario(spec(), 2).experiments:
        nominal = nominal_duration(protocols[experiment.protocol], equipment, 3.0)
        slack = (experiment.deadline_min - experiment.submit_at_min) / nominal
        assert 1.2 - 1e-3 <= slack <= 1.5 + 1e-3


def test_nominal_duration() -> None:
    equipment = load_equipment_config(Path("configs/equipment.yaml"))  # media 15 min
    protocol = Protocol("p", "c", (ProtocolStep(Operation.INCUBATE, 60), ProtocolStep(Operation.MEDIA_EXCHANGE),
                                   ProtocolStep(Operation.ARCHIVE)))

    assert nominal_duration(protocol, equipment, transport_min=3.0) == 60 + 15 + 0 + 3 * 3


def test_generated_faults_are_valid_and_never_overlap_on_one_unit() -> None:
    scenario = generate_scenario(spec(), 5)
    equipment = load_equipment_config(Path("configs/equipment.yaml"), spec().equipment_overrides)
    ids = equipment_ids(equipment)

    assert len(scenario.faults) == 4
    for fault in scenario.faults:
        assert 100 <= fault.start_min <= 200 and 30 <= fault.duration_min <= 60
        assert any(fault.equipment_id in ids[kind] for kind in VALID_TARGETS[fault.fault_type])
    for i, a in enumerate(scenario.faults):
        for b in scenario.faults[i + 1:]:
            if a.equipment_id == b.equipment_id:
                assert a.start_min + a.duration_min <= b.start_min or b.start_min + b.duration_min <= a.start_min


def test_impossible_fault_plan_is_reported() -> None:
    crowded = spec(faults=FaultPlan(20, (FaultType.IMAGING_FAILURE,), (100, 101), (500, 600)))

    with pytest.raises(ConfigurationError, match="could only place"):
        generate_scenario(crowded, 1)


def test_generated_scenario_round_trips_through_yaml() -> None:
    scenario = generate_scenario(spec(), 7)

    reloaded = parse_scenario(yaml.safe_load(scenario_to_yaml(scenario)))

    assert reloaded.experiments == scenario.experiments
    assert reloaded.faults == scenario.faults
    assert reloaded.equipment_overrides == scenario.equipment_overrides


@pytest.mark.parametrize("overrides", [
    {"plates": 0}, {"experiment_plates": (5, 2)}, {"protocol_mix": {}}, {"deadline_slack": (0.9, 1.2)},
])
def test_invalid_workloads(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        spec(**overrides)


def test_unknown_protocol_in_mix() -> None:
    with pytest.raises(ConfigurationError, match="unknown protocols"):
        generate_scenario(spec(protocol_mix={"nope": 1}), 1)


# ------------------------------------------------------------ statistics
def rows() -> list[dict[str, Any]]:
    data = {("fifo", 1): 100, ("fifo", 2): 120, ("cost", 1): 90, ("cost", 2): 120}
    return [{"workload": "W", "scheduler": s, "seed": seed, "makespan_min": v} for (s, seed), v in data.items()]


def test_aggregate_mean_and_std() -> None:
    summary = {r["scheduler"]: r for r in aggregate(rows(), metrics=["makespan_min"])}

    assert summary["fifo"]["makespan_min"] == 110 and summary["fifo"]["runs"] == 2
    assert summary["fifo"]["makespan_min_std"] == pytest.approx(14.142, rel=1e-3)


def test_paired_comparison_counts_seeds() -> None:
    [entry] = paired_comparison(rows(), "makespan_min", baseline="fifo")

    assert (entry["scheduler"], entry["better"], entry["equal"], entry["worse"]) == ("cost", 1, 1, 0)


def test_repository_benchmark_plan_loads() -> None:
    plan = load_benchmark_plan(Path("configs/benchmarks.yaml"))

    assert set(plan.workloads) == {"A", "B", "C", "D"}
    assert plan.workloads["D"].faults is not None and plan.workloads["D"].faults.count == 6
    assert plan.schedulers == ("fifo", "priority", "deadline", "cost")


def test_benchmark_plan_rejects_unknown_scheduler(tmp_path: Path) -> None:
    path = tmp_path / "b.yaml"
    path.write_text("schedulers: [fifo, magic]\nworkloads: {}\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="unknown scheduler 'magic'"):
        load_benchmark_plan(path)


def test_run_one_produces_a_complete_row() -> None:
    small = replace(load_benchmark_plan(Path("configs/benchmarks.yaml")).workloads["A"], plates=3)

    row = run_one(small, "cost", seed=1)

    assert row["tasks_completed"] == row["tasks_total"] and not row["stalled"]
    assert row["scheduler"] == "cost" and 0 <= row["deadline_miss_rate"] <= 1


# ------------------------------------------------------ deferred arrival
def test_arrival_waits_while_storage_is_out_of_service(make_lab) -> None:
    lab = make_lab()
    log: list[Any] = []
    lab.engine.bus.subscribe(DEFERRED_EVENT, log.append)
    protocol = Protocol("p", "c", (ProtocolStep(Operation.IMAGE), ProtocolStep(Operation.ARCHIVE)))
    lab.schedule_fault(FaultSpec(FaultType.COMMUNICATION_TIMEOUT, "STORAGE_01", 0, duration_min=40))
    lab.schedule_experiment(Experiment("EXP", protocol, 2, 10.0))  # storage is out of service by then

    summary = lab.run()

    assert log and "STORAGE" in log[0].payload["reason"]
    assert summary.tasks_completed == summary.tasks_total == 4
    assert lab.state.experiment("EXP").status == "COMPLETED"
