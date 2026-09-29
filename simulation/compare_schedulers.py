"""Run one scenario under every scheduling policy and compare the results.

Usage (from the project root):
    python simulation/compare_schedulers.py simulation/scenarios/basic_demo.yaml

Every policy sees the identical workload and seed. The table reports what
happened on *this* scenario only; it is not evidence that one policy is better
in general. Phase 24 runs the systematic benchmarks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bioflow.core.exceptions import BioFlowError
from bioflow.scenario import load_scenario, run_scenario
from bioflow.scheduling.registry import SCHEDULERS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare scheduling policies on one scenario.")
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args(argv)

    try:
        scenario = load_scenario(args.scenario)
        summaries = {name: run_scenario(scenario, scheduler=name) for name in SCHEDULERS}
    except BioFlowError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    experiment_ids = [spec.experiment_id for spec in scenario.experiments]
    header = f"{'Scheduler':<10}{'Makespan':>10}{'Misses':>8}" + "".join(f"{e:>12}" for e in experiment_ids)
    print(f"Scenario: {scenario.name} (seed {scenario.seed}) -- finish time per experiment, * = late\n")
    print(header)
    print("-" * len(header))
    for name, summary in summaries.items():
        cells = []
        for result in summary.experiments:
            mark = "*" if result.late else " "
            cells.append(f"{result.finished_at:>11.1f}{mark}" if result.finished_at is not None else f"{'-':>12}")
        print(f"{name:<10}{summary.makespan:>10.1f}{summary.deadline_violations:>8}" + "".join(cells))
    deadlines = "  ".join(f"{s.experiment_id}={s.deadline_min}" for s in scenario.experiments)
    print(f"\nDeadlines: {deadlines}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
