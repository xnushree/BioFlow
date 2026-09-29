"""Benchmark the scheduling policies on generated workloads.

Usage (from the project root):
    python simulation/benchmark.py                          # everything in configs/benchmarks.yaml
    python simulation/benchmark.py --workloads A B --seeds 2
    python simulation/benchmark.py --save-scenarios         # also write each generated scenario as YAML

Outputs (under --out, default results/):
    benchmarks/runs.csv          one row per (workload, scheduler, seed)
    benchmarks/summary.csv       mean and std per (workload, scheduler)
    reports/benchmark_report.md  tables, paired comparisons and caveats
    plots/*.png                  charts
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path
from typing import Any

from bioflow.analytics.performance import METRICS, aggregate, load_benchmark_plan, paired_comparison, run_benchmark
from bioflow.analytics.plots import save_metric_chart, save_overview
from bioflow.core.exceptions import BioFlowError
from bioflow.workload import generate_scenario, scenario_to_yaml

OVERVIEW_METRICS = ["makespan_min", "mean_task_wait_min", "deadline_miss_rate", "throughput_plates_per_hour"]
PAIRED_METRICS = ["makespan_min", "mean_task_wait_min", "deadline_miss_rate"]


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _fmt(value: float) -> str:
    return f"{value:.2f}" if abs(value) < 10 else f"{value:,.0f}"


def report(plan, runs, summary, workloads, seeds, elapsed: float) -> str:  # noqa: ANN001
    lines = [
        "# Scheduler benchmark report", "",
        f"{len(runs)} simulation runs: {len(workloads)} workloads x {len(plan.schedulers)} schedulers x "
        f"{len(seeds)} seeds (seeds {', '.join(map(str, seeds))}), {elapsed:.0f} s wall time.",
        "Every scheduler ran on exactly the same generated scenarios, so comparisons are paired.", "",
        "Values are **mean ± standard deviation across seeds**. Where two schedulers' means differ by less "
        "than their spread, the data does not show a real difference.", "",
    ]
    for workload in workloads:
        spec = plan.workloads[workload]
        lines += [f"## Workload {workload}", "", spec.description, ""]
        header = ["Scheduler", *[METRICS[m][0] for m in METRICS]]
        lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        for row in [r for r in summary if r["workload"] == workload]:
            cells = [f"{_fmt(row[m])} ± {_fmt(row[m + '_std'])}" for m in METRICS]
            lines.append(f"| {row['scheduler']} | " + " | ".join(cells) + " |")
        wl_runs = [r for r in runs if r["workload"] == workload]
        lines += ["", "Integrity: "
                  f"{sum(r['tasks_completed'] for r in wl_runs)}/{sum(r['tasks_total'] for r in wl_runs)} tasks "
                  f"completed across all runs, {sum(r['stalled'] for r in wl_runs)} stalled runs, "
                  f"{sum(r['faults_detected'] for r in wl_runs)}/{sum(r['faults_injected'] for r in wl_runs)} "
                  f"injected faults detected, {sum(r['false_alarms'] for r in wl_runs)} false alarms, "
                  f"{sum(r['unrecoverable'] for r in wl_runs)} unrecoverable.", ""]
    lines += ["## Paired comparison against FIFO", "",
              "On how many seeds each scheduler did better / the same / worse than FIFO on the same workload.", "",
              "| Workload | Scheduler | " + " | ".join(METRICS[m][0] for m in PAIRED_METRICS) + " |",
              "|---|---|" + "---|" * len(PAIRED_METRICS)]
    comparisons = {m: paired_comparison(runs, m) for m in PAIRED_METRICS}
    for index, entry in enumerate(comparisons[PAIRED_METRICS[0]]):
        cells = [f"{c['better']} / {c['equal']} / {c['worse']}"
                 for c in (comparisons[m][index] for m in PAIRED_METRICS)]
        lines.append(f"| {entry['workload']} | {entry['scheduler']} | " + " | ".join(cells) + " |")
    lines += ["", "## Caveats", "",
              "- Workloads are synthetic and generated from the recipes in `configs/benchmarks.yaml`; results "
              "describe this simulated lab, not any real facility.",
              f"- {len(seeds)} seeds per cell is a small sample; treat differences within one standard deviation "
              "as noise.",
              "- The cost-based scheduler uses weights tuned on workload C (`configs/scheduling.yaml`, see docs/benchmarking.md).",
              "- Deadlines are set relative to each protocol's nominal duration; the miss rate depends on that "
              "choice as much as on the scheduler.", "",
              "## Charts", "", "![Overview](../plots/benchmark_overview.png)", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark scheduling policies on generated workloads.")
    parser.add_argument("--config", type=Path, default=Path("configs/benchmarks.yaml"))
    parser.add_argument("--workloads", nargs="+", help="subset of workloads (default: all)")
    parser.add_argument("--seeds", type=int, help="use only the first N seeds of the config")
    parser.add_argument("--out", type=Path, default=Path("results"))
    parser.add_argument("--save-scenarios", action="store_true", help="write each generated scenario as YAML")
    args = parser.parse_args(argv)
    logging.getLogger("bioflow").setLevel(logging.ERROR)  # keep fault warnings out of the progress output

    try:
        plan = load_benchmark_plan(args.config)
        workloads = args.workloads or list(plan.workloads)
        seeds = list(plan.seeds[: args.seeds] if args.seeds else plan.seeds)
        total = len(workloads) * len(seeds) * len(plan.schedulers)
        started = time.perf_counter()
        done = 0

        def progress(row: dict[str, Any]) -> None:
            nonlocal done
            done += 1
            print(f"[{done:>3}/{total}] {row['workload']} seed {row['seed']} {row['scheduler']:<9} "
                  f"makespan {row['makespan_min']:8.1f}  late {row['deadline_miss_rate']:5.0%}  "
                  f"({row['wall_time_s']:.1f}s)", flush=True)

        runs = run_benchmark(plan, workloads, seeds, progress)
        elapsed = time.perf_counter() - started
    except BioFlowError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    summary = aggregate(runs)
    write_csv(runs, args.out / "benchmarks" / "runs.csv")
    write_csv(summary, args.out / "benchmarks" / "summary.csv")
    plots = args.out / "plots"
    save_overview(summary, plots / "benchmark_overview.png", workloads, plan.schedulers, OVERVIEW_METRICS)
    for metric in METRICS:
        save_metric_chart(summary, metric, plots / f"benchmark_{metric}.png", workloads, plan.schedulers)
    report_path = args.out / "reports" / "benchmark_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report(plan, runs, summary, workloads, seeds, elapsed), encoding="utf-8")
    if args.save_scenarios:
        for workload in workloads:
            for seed in seeds:
                scenario = generate_scenario(plan.workloads[workload], seed)
                target = args.out / "benchmarks" / "scenarios" / f"{scenario.name}.yaml"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(scenario_to_yaml(scenario), encoding="utf-8")
    print(f"\n{len(runs)} runs in {elapsed:.0f} s. Report: {report_path}  Charts: {plots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
