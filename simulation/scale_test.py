"""Large-scale runs: how simulation cost grows with the number of plates.

Usage (from the project root):
    python simulation/scale_test.py                        # 100, 500, 1000, 2000, 5000 plates
    python simulation/scale_test.py --sizes 100 1000
    python simulation/scale_test.py --sizes 1000 --profile # cProfile hotspots for one size
    python simulation/scale_test.py --memory               # add a (slower) tracemalloc pass per size

Arrivals are spread so the lab runs at about 80 % of its capacity (~1.5 plates/hour), not in an
ever-growing backlog, so the numbers describe the simulator, not a pathological workload.
Writes results/reports/scale_report.md (unless --profile).
"""

from __future__ import annotations

import argparse
import cProfile
import io
import logging
import pstats
import sys
import time
import tracemalloc
from dataclasses import replace
from pathlib import Path
from typing import Any

from bioflow.scenario import build_laboratory
from bioflow.workload import WorkloadSpec, generate_scenario

MINUTES_PER_PLATE = 40.0  # ~1.5 plates/hour: about 80 % of the 4-robot lab's capacity, so no runaway backlog


def scale_spec(plates: int) -> WorkloadSpec:
    return WorkloadSpec(
        name=f"S{plates}", description=f"Scale test with {plates} plates", plates=plates,
        experiment_plates=(10, 30), arrival_window_min=plates * MINUTES_PER_PLATE,
        protocol_mix={"basic_experiment": 2, "imaging_experiment": 1},
        equipment_config=Path("configs/equipment.yaml"), protocol_dir=Path("protocols"),
        equipment_overrides={"robots": {"count": 4}, "incubators": {"count": 4},
                             "media_stations": {"count": 2}, "imaging_stations": {"count": 2}},
        laboratory_config=Path("configs/laboratory.yaml"), scheduling_config=Path("configs/scheduling.yaml"),
        fault_config=Path("configs/faults.yaml"),
    )


def build(plates: int, scheduler: str):  # noqa: ANN201
    return build_laboratory(replace(generate_scenario(scale_spec(plates), seed=1), scheduler=scheduler))


def measure(plates: int, scheduler: str) -> dict[str, Any]:
    lab = build(plates, scheduler)
    dispatch_time = 0.0
    original = lab.dispatcher._dispatch_pass  # noqa: SLF001  (timing wrapper for the report only)

    def timed_pass() -> None:
        nonlocal dispatch_time
        started = time.perf_counter()
        original()
        dispatch_time += time.perf_counter() - started

    lab.dispatcher._dispatch_pass = timed_pass  # type: ignore[method-assign]  # noqa: SLF001
    started = time.perf_counter()
    summary = lab.run()
    wall = time.perf_counter() - started
    return {
        "plates": plates, "tasks": summary.tasks_total, "completed": summary.tasks_completed,
        "sim_days": summary.makespan / 1440, "events": summary.events_processed,
        "bus_messages": lab.engine.bus.stats.total_published, "wall_s": wall,
        "events_per_s": summary.events_processed / wall, "scheduling_s": dispatch_time,
    }


def peak_memory_mb(plates: int, scheduler: str) -> float:
    tracemalloc.start()
    build(plates, scheduler).run()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / 1e6


def profile(plates: int, scheduler: str) -> str:
    lab = build(plates, scheduler)
    profiler = cProfile.Profile()
    profiler.runcall(lab.run)
    out = io.StringIO()
    pstats.Stats(profiler, stream=out).sort_stats("cumulative").print_stats(25)
    return out.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure simulator performance at scale.")
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 500, 1000, 2000, 5000])
    parser.add_argument("--scheduler", default="fifo")
    parser.add_argument("--profile", action="store_true", help="print cProfile hotspots for the first size")
    parser.add_argument("--memory", action="store_true", help="also measure peak Python heap (slower)")
    args = parser.parse_args(argv)
    logging.getLogger("bioflow").setLevel(logging.ERROR)

    if args.profile:
        print(profile(args.sizes[0], args.scheduler))
        return 0

    rows = []
    for plates in args.sizes:
        row = measure(plates, args.scheduler)
        if args.memory:
            row["peak_heap_mb"] = peak_memory_mb(plates, args.scheduler)
        rows.append(row)
        print(f"{plates:>6} plates  {row['tasks']:>6} tasks ({row['completed']} done)  "
              f"{row['sim_days']:6.1f} sim days  {row['events']:>9,} events  {row['wall_s']:7.1f} s  "
              f"{row['events_per_s']:>9,.0f} ev/s  scheduling {row['scheduling_s']:6.1f} s"
              + (f"  heap {row['peak_heap_mb']:.0f} MB" if args.memory else ""), flush=True)

    columns = ["plates", "tasks", "completed", "sim_days", "events", "bus_messages", "wall_s", "events_per_s",
               "scheduling_s", *(["peak_heap_mb"] if args.memory else [])]
    lines = ["# Scale test", "", f"Scheduler `{args.scheduler}`, seed 1, arrivals every ~{MINUTES_PER_PLATE:g} "
             "min per plate (near lab capacity), 4 robots on the map with monitoring and fault detection on.", "",
             "| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in rows:
        lines.append("| " + " | ".join(f"{row[c]:,.1f}" if isinstance(row[c], float) else f"{row[c]:,}"
                                       for c in columns) + " |")
    path = Path("results/reports/scale_report.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
