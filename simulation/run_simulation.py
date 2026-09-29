"""Run a BioFlow-X scenario and print its summary.

Usage (from the project root):
    python simulation/run_simulation.py simulation/scenarios/basic_demo.yaml
    python simulation/run_simulation.py simulation/scenarios/basic_demo.yaml --scheduler fifo --log-level INFO
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from bioflow.core.exceptions import BioFlowError
from bioflow.scenario import load_scenario, run_scenario
from bioflow.scheduling.registry import SCHEDULERS
from bioflow.telemetry.logger import VALID_LEVELS, configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a BioFlow-X simulation scenario.")
    parser.add_argument("scenario", type=Path, help="scenario YAML file")
    parser.add_argument("--scheduler", choices=sorted(SCHEDULERS), help="override the scenario's scheduler")
    parser.add_argument("--until", type=float, help="stop at this simulation time (minutes)")
    parser.add_argument("--log-level", default="WARNING", choices=VALID_LEVELS)
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    try:
        scenario = load_scenario(args.scenario)
        started = time.perf_counter()
        summary = run_scenario(scenario, scheduler=args.scheduler, until=args.until)
        elapsed = time.perf_counter() - started
    except BioFlowError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Scenario:           {scenario.name}  (seed {scenario.seed})")
    print(summary.format())
    print(f"\nWall-clock time:    {elapsed:.3f} s")
    return 2 if summary.stalled else 0


if __name__ == "__main__":
    sys.exit(main())
