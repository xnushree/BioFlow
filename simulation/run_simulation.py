"""Run a BioFlow-X scenario and print its summary.

Usage (from the project root):
    python simulation/run_simulation.py simulation/scenarios/basic_demo.yaml
    python simulation/run_simulation.py simulation/scenarios/basic_demo.yaml --scheduler cost --log-level INFO
    python simulation/run_simulation.py simulation/scenarios/multiple_failures.yaml \
        --telemetry standard --telemetry-out results/simulations/multiple_failures.jsonl
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from bioflow.core.exceptions import BioFlowError
from bioflow.database import Database, RunRepository
from bioflow.scenario import build_laboratory, load_scenario
from bioflow.scheduling.registry import SCHEDULERS
from bioflow.telemetry.logger import VALID_FORMATS, VALID_LEVELS, attach_clock, configure_logging
from bioflow.telemetry.recorder import TelemetryLevel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a BioFlow-X simulation scenario.")
    parser.add_argument("scenario", type=Path, help="scenario YAML file")
    parser.add_argument("--scheduler", choices=sorted(SCHEDULERS), help="override the scenario's scheduler")
    parser.add_argument("--until", type=float, help="stop at this simulation time (minutes)")
    parser.add_argument("--log-level", default="WARNING", choices=VALID_LEVELS)
    parser.add_argument("--log-format", default="text", choices=VALID_FORMATS)
    parser.add_argument("--log-file", type=Path, help="also write logs to this file")
    parser.add_argument("--telemetry", choices=[level.value for level in TelemetryLevel],
                        help="record structured events at this detail level")
    parser.add_argument("--telemetry-out", type=Path, help="write recorded events here as JSON Lines")
    parser.add_argument("--db", type=Path, help="save the run to this SQLite database")
    parser.add_argument("--label", help="label for the saved run (default: scenario/scheduler)")
    args = parser.parse_args(argv)
    if args.telemetry_out and not args.telemetry:
        parser.error("--telemetry-out needs --telemetry")

    configure_logging(args.log_level, args.log_file, fmt=args.log_format)
    try:
        scenario = load_scenario(args.scenario)
        lab = build_laboratory(scenario, scheduler=args.scheduler,
                               telemetry=TelemetryLevel(args.telemetry) if args.telemetry else None)
        attach_clock(lab.engine.clock)
        started = time.perf_counter()
        summary = lab.run(until=args.until)
        elapsed = time.perf_counter() - started
    except BioFlowError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Scenario:           {scenario.name}  (seed {scenario.seed})")
    print(summary.format())
    print()
    print(f"Wall-clock time:    {elapsed:.3f} s")
    if args.telemetry_out and lab.recorder is not None:
        written = lab.recorder.write_jsonl(args.telemetry_out)
        print(f"Telemetry:          {written} records ({args.telemetry}) -> {args.telemetry_out}")
    if args.db:
        with Database(args.db) as database:
            run_id = RunRepository(database).save_run(
                lab, summary, label=args.label or f"{scenario.name}/{summary.scheduler}",
                scenario=scenario.name, seed=scenario.seed,
            )
        print(f"Saved:              run {run_id} -> {args.db}")
    return 2 if summary.stalled else 0


if __name__ == "__main__":
    sys.exit(main())
