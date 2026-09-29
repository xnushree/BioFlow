"""Scheduler benchmarking: run workloads x schedulers x seeds, collect metrics, aggregate honestly.

Statistics reported per (workload, scheduler):
    * mean and standard deviation across seeds;
    * a *paired* comparison against a baseline scheduler: on how many seeds was
      it better, equal, or worse on the same generated workload.

A difference smaller than the seed-to-seed spread is not evidence of anything,
and the report says so rather than naming a winner.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import yaml

from bioflow.core.exceptions import ConfigurationError
from bioflow.core.validation import suggest
from bioflow.faults.fault import FaultType
from bioflow.scenario import build_laboratory
from bioflow.scheduling.registry import SCHEDULERS
from bioflow.workload import FaultPlan, WorkloadSpec, generate_scenario

Row = dict[str, Any]

# metric -> (label, lower is better?)
METRICS: dict[str, tuple[str, bool]] = {
    "makespan_min": ("Makespan (min)", True),
    "mean_task_wait_min": ("Mean task wait (min)", True),
    "max_task_wait_min": ("Max task wait (min)", True),
    "deadline_miss_rate": ("Experiments late (fraction)", True),
    "mean_lateness_min": ("Mean lateness of late experiments (min)", True),
    "throughput_plates_per_hour": ("Throughput (plates/hour)", False),
    "mean_robot_utilization": ("Robot utilization", False),
    "mean_station_utilization": ("Station utilization", False),
    "mean_ready_queue": ("Mean ready queue", True),
    "tasks_rescheduled": ("Tasks rescheduled", True),
    "mean_recovery_min": ("Mean recovery time (min)", True),
}


@dataclass(frozen=True)
class BenchmarkPlan:
    workloads: dict[str, WorkloadSpec]
    schedulers: tuple[str, ...]
    seeds: tuple[int, ...]


# ------------------------------------------------------------------ config
def load_benchmark_plan(path: Path) -> BenchmarkPlan:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"Benchmark config not found: {path}") from None
    if not isinstance(data, Mapping):
        raise ConfigurationError(f"{path.name}: expected a mapping")
    defaults = data.get("defaults") or {}
    schedulers = tuple(data.get("schedulers") or SCHEDULERS)
    for name in schedulers:
        if name not in SCHEDULERS:
            raise ConfigurationError(f"{path.name}: unknown scheduler {name!r}{suggest(name, SCHEDULERS)}")
    workloads = {str(name): _workload(str(name), raw, defaults) for name, raw in (data.get("workloads") or {}).items()}
    if not workloads:
        raise ConfigurationError(f"{path.name}: no workloads defined")
    return BenchmarkPlan(workloads, schedulers, tuple(int(s) for s in data.get("seeds") or (1,)))


def _workload(name: str, raw: Mapping[str, Any], defaults: Mapping[str, Any]) -> WorkloadSpec:
    merged = {**defaults, **raw}

    def path(key: str) -> Path | None:
        return Path(merged[key]) if merged.get(key) else None

    faults = merged.get("faults")
    try:
        return WorkloadSpec(
            name=name, description=str(merged.get("description", "")), plates=int(merged["plates"]),
            experiment_plates=tuple(merged["experiment_plates"]),  # type: ignore[arg-type]
            arrival_window_min=float(merged["arrival_window_min"]),
            protocol_mix={str(k): float(v) for k, v in merged["protocol_mix"].items()},
            equipment_config=Path(merged["equipment_config"]), protocol_dir=Path(merged["protocol_dir"]),
            priority_range=tuple(merged.get("priority_range", (0, 0))),  # type: ignore[arg-type]
            deadline_slack=tuple(merged["deadline_slack"]) if merged.get("deadline_slack") else None,  # type: ignore[arg-type]
            equipment_overrides=merged.get("equipment") or {},
            laboratory_config=path("laboratory_config"), scheduling_config=path("scheduling_config"),
            fault_config=path("fault_config"),
            transport_estimate_min=float(merged.get("transport_estimate_min", 3.0)),
            faults=FaultPlan(
                count=int(faults["count"]), types=tuple(FaultType(t) for t in faults["types"]),
                window_min=tuple(faults["window_min"]), duration_min=tuple(faults["duration_min"]),  # type: ignore[arg-type]
            ) if faults else None,
        )
    except KeyError as error:
        raise ConfigurationError(f"workload {name}: missing setting {error}") from None


# ------------------------------------------------------------------ running
def run_one(spec: WorkloadSpec, scheduler: str, seed: int) -> Row:
    scenario = generate_scenario(spec, seed)
    started = time.perf_counter()
    lab = build_laboratory(replace(scenario, scheduler=scheduler))
    summary = lab.run()
    wall = time.perf_counter() - started
    metrics = summary.metrics
    assert metrics is not None
    late = [r for r in summary.experiments if r.late]
    with_deadline = [r for r in summary.experiments if r.deadline is not None]
    recovery = summary.recovery
    detection = summary.faults
    return {
        "workload": spec.name, "scheduler": scheduler, "seed": seed,
        "plates": spec.plates, "experiments": len(summary.experiments),
        "tasks_total": summary.tasks_total, "tasks_completed": summary.tasks_completed,
        "completion_rate": summary.tasks_completed / summary.tasks_total if summary.tasks_total else 0.0,
        "stalled": summary.stalled,
        "makespan_min": summary.makespan,
        "mean_task_wait_min": metrics.mean_task_wait_min, "max_task_wait_min": metrics.max_task_wait_min,
        "deadline_misses": len(late),
        "deadline_miss_rate": len(late) / len(with_deadline) if with_deadline else 0.0,
        "mean_lateness_min": mean(r.finished_at - r.deadline for r in late) if late else 0.0,  # type: ignore[operator]
        "throughput_plates_per_hour": metrics.throughput_plates_per_hour,
        "mean_robot_utilization": metrics.mean_robot_utilization,
        "mean_station_utilization": metrics.mean_station_utilization,
        "mean_incubator_utilization": metrics.mean_incubator_utilization,
        "mean_ready_queue": metrics.mean_ready_queue, "max_ready_queue": metrics.max_ready_queue,
        "tasks_rescheduled": metrics.tasks_rescheduled,
        "faults_injected": len(scenario.faults),
        "faults_detected": len(detection.matches) if detection else 0,
        "false_alarms": len(detection.false_positives) if detection else 0,
        "mean_recovery_min": recovery.mean_recovery_min if recovery and recovery.mean_recovery_min else 0.0,
        "unrecoverable": recovery.unrecoverable if recovery else 0,
        "deadlocks_resolved": summary.motion.deadlocks if summary.motion else 0,
        "events_processed": summary.events_processed,
        "wall_time_s": wall,
    }


def run_benchmark(plan: BenchmarkPlan, workloads: Iterable[str] | None = None, seeds: Sequence[int] | None = None,
                  progress: Callable[[Row], None] | None = None) -> list[Row]:
    rows = []
    for name in workloads or plan.workloads:
        if name not in plan.workloads:
            raise ConfigurationError(f"unknown workload {name!r}{suggest(name, plan.workloads)}")
        for seed in seeds or plan.seeds:
            for scheduler in plan.schedulers:
                row = run_one(plan.workloads[name], scheduler, seed)
                rows.append(row)
                if progress is not None:
                    progress(row)
    return rows


# -------------------------------------------------------------- aggregation
def aggregate(rows: list[Row], metrics: Iterable[str] = METRICS) -> list[Row]:
    """Mean and standard deviation of each metric per (workload, scheduler)."""
    groups: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        groups.setdefault((row["workload"], row["scheduler"]), []).append(row)
    out = []
    for (workload, scheduler), group in groups.items():
        entry: Row = {"workload": workload, "scheduler": scheduler, "runs": len(group)}
        for metric in metrics:
            values = [float(r[metric]) for r in group]
            entry[metric] = mean(values)
            entry[f"{metric}_std"] = stdev(values) if len(values) > 1 else 0.0
        out.append(entry)
    return out


def paired_comparison(rows: list[Row], metric: str, baseline: str = "fifo",
                      tolerance: float = 1e-6) -> list[Row]:
    """Per workload and scheduler: seeds where it beat / tied / lost to ``baseline`` on the same workload."""
    lower_is_better = METRICS[metric][1]
    by_key = {(r["workload"], r["scheduler"], r["seed"]): r[metric] for r in rows}
    results = []
    for workload in sorted({r["workload"] for r in rows}):
        for scheduler in sorted({r["scheduler"] for r in rows if r["workload"] == workload} - {baseline}):
            better = equal = worse = 0
            for seed in sorted({r["seed"] for r in rows if r["workload"] == workload}):
                if (workload, baseline, seed) not in by_key or (workload, scheduler, seed) not in by_key:
                    continue
                delta = by_key[(workload, scheduler, seed)] - by_key[(workload, baseline, seed)]
                if abs(delta) <= tolerance:
                    equal += 1
                elif (delta < 0) == lower_is_better:
                    better += 1
                else:
                    worse += 1
            results.append({"workload": workload, "scheduler": scheduler, "metric": metric,
                            "better": better, "equal": equal, "worse": worse})
    return results
