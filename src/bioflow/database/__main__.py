"""Browse saved runs.

Usage:
    python -m bioflow.database results/bioflow.db                # list runs
    python -m bioflow.database results/bioflow.db --run 3        # one run's summary
    python -m bioflow.database results/bioflow.db --compare 1 2  # side by side
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bioflow.core.exceptions import BioFlowError
from bioflow.database import Database, RunRepository

_COMPARE_COLUMNS = ("run_id", "scheduler", "makespan", "deadline_misses", "mean_robot_utilization",
                    "mean_task_wait_min", "throughput_plates_per_hour", "tasks_rescheduled")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bioflow.database", description="Browse saved runs.")
    parser.add_argument("db", type=Path)
    parser.add_argument("--run", type=int, help="show one run's full summary")
    parser.add_argument("--compare", type=int, nargs="+", help="compare runs side by side")
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"error: {args.db} does not exist", file=sys.stderr)
        return 1
    try:
        with Database(args.db) as database:
            repo = RunRepository(database)
            if args.run is not None:
                print(repo.run(args.run)["summary_text"])
            elif args.compare:
                rows = repo.compare_runs(args.compare)
                print("  ".join(f"{c:>14}" for c in _COMPARE_COLUMNS))
                for row in rows:
                    print("  ".join(_cell(row.get(c)) for c in _COMPARE_COLUMNS))
            else:
                for row in repo.runs():
                    print(f"{row['run_id']:>4}  {row['label']:<32} {row['scheduler']:<9} "
                          f"makespan {row['makespan']:8.1f}  tasks {row['tasks_completed']}/{row['tasks_total']}"
                          f"  misses {row['deadline_misses']}")
    except BioFlowError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _cell(value: object) -> str:
    return f"{value:>14.3f}" if isinstance(value, float) else f"{value!s:>14}"


if __name__ == "__main__":
    sys.exit(main())
