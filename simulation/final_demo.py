"""Scripted end-to-end demonstration of BioFlow-X (Phase 30).

Usage (from the project root):
    python simulation/final_demo.py
    python simulation/final_demo.py --scenario simulation/scenarios/final_demo.yaml --compare cost

What it shows, in order:
  1. the laboratory and the workload being loaded;
  2. the run pausing at checkpoints so the live twin can be inspected;
  3. an injected robot failure and incubator failure, how they were detected
     (from symptoms only) and what automatic recovery did about them;
  4. the final results;
  5. the identical workload re-run under every scheduling policy, and without
     faults, for a quantitative comparison;
  6. a snapshot of the 2D digital twin during the incubator failure.

Everything is deterministic: the same scenario and seed print the same numbers.
Writes results/reports/final_demo.md.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from bioflow.analytics.summary import RunSummary
from bioflow.core.exceptions import BioFlowError
from bioflow.laboratory import Laboratory
from bioflow.scenario import Scenario, build_laboratory, load_scenario
from bioflow.scheduling.registry import SCHEDULERS
from bioflow.service import SimulationService
from bioflow.telemetry.recorder import TelemetryLevel

DEFAULT_SCENARIO = Path("simulation/scenarios/final_demo.yaml")
REPORT = Path("results/reports/final_demo.md")
TELEMETRY = Path("results/simulations/final_demo.jsonl")
TWIN = Path("results/plots/twin_final_demo.png")


def banner(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def snapshot(lab: Laboratory) -> str:
    """One-line view of the twin: plates by state, equipment by state."""
    plates = Counter(str(plate.state) for plate in lab.state.plates.values())
    equipment = Counter(str(item.state) for item in lab.state.equipment.values())
    done = sum(1 for task in lab.state.tasks if task.completed_at is not None)
    return (f"t={lab.engine.now:7.1f} min | tasks done {done:>3} of {len(list(lab.state.tasks))} created | plates "
            + ", ".join(f"{k} {v}" for k, v in sorted(plates.items()))
            + "\n" + " " * 16 + "| equipment " + ", ".join(f"{k} {v}" for k, v in sorted(equipment.items())))


def fault_story(lab: Laboratory) -> list[str]:
    """Pair each injected fault (ground truth) with its detection and recovery."""
    lines = []
    detections = lab.detector.detections
    recoveries = {r.detection_id: r for r in lab.recovery.recoveries}
    for fault in lab.injector.faults:
        lines.append(f"* INJECTED {fault.fault_type} on {fault.equipment_id} at t={fault.injected_at:.1f} "
                     f"(repaired t={fault.repaired_at:.1f})" if fault.repaired_at is not None
                     else f"* INJECTED {fault.fault_type} on {fault.equipment_id} at t={fault.injected_at:.1f}")
        found = [d for d in detections if d.equipment_id == fault.equipment_id
                 and fault.injected_at is not None and d.detected_at >= fault.injected_at]
        if not found:
            lines.append("    not detected")
            continue
        detection = found[0]
        lines.append(f"    DETECTED  t={detection.detected_at:.1f} (+{detection.detected_at - fault.injected_at:.1f} min) "
                     f"as {detection.fault_type}: {detection.evidence}")
        recovery = recoveries.get(detection.detection_id)
        if recovery is None:
            continue
        for action in recovery.actions[:6]:
            lines.append(f"    RECOVERY  {action}")
        if len(recovery.actions) > 6:
            lines.append(f"    RECOVERY  ... {len(recovery.actions) - 6} more actions")
        finished = (f"completed at t={recovery.completed_at:.1f}" if recovery.completed_at is not None
                    else "still open")
        lines.append(f"    RESULT    {len(recovery.affected_plates)} plates affected, "
                     f"{recovery.rescheduled_tasks} tasks rescheduled, recovery {finished}"
                     + (" (UNRECOVERABLE)" if recovery.unrecoverable else ""))
    return lines


def save_twin_snapshot(scenario_path: Path, at_min: float, out: Path) -> Path | None:
    """Render the dashboard's 2D twin at ``at_min`` (the same drawing code the dashboard uses)."""
    sys.path.insert(0, str(Path(__file__).parents[1] / "dashboard"))
    from components.twin import draw_twin  # the dashboard's own module
    import matplotlib.pyplot as plt

    service = SimulationService()
    service.load(scenario_path)
    service.step(at_min)
    plan = service.floor_plan()
    if plan is None:
        return None
    figure = draw_twin(plan, service.equipment(), service.robots())
    figure.axes[0].set_title(f"{scenario_path.stem} at t = {at_min:g} min", pad=12)
    out.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(figure)
    return out


def row(label: str, summary: RunSummary, wall_s: float) -> dict[str, Any]:
    metrics = summary.metrics
    assert metrics is not None
    late = [r for r in summary.experiments if r.late]
    return {
        "run": label,
        "tasks": f"{summary.tasks_completed}/{summary.tasks_total}",
        "makespan_min": round(summary.makespan, 1),
        "deadline_misses": len(late),
        "total_lateness_min": round(sum(r.finished_at - r.deadline for r in late), 1),  # type: ignore[operator]
        "mean_task_wait_min": round(metrics.mean_task_wait_min, 2),
        "max_ready_queue": metrics.max_ready_queue,
        "robot_util_%": round(100 * metrics.mean_robot_utilization, 1),
        "throughput_plates_h": round(metrics.throughput_plates_per_hour, 2),
        "faults_detected": f"{len(summary.faults.matches)}/{len(summary.faults.matches) + len(summary.faults.missed)}"
        if summary.faults else "-",
        "plates_affected": summary.recovery.plates_affected if summary.recovery else 0,
        "tasks_rescheduled": metrics.tasks_rescheduled,
        "wall_s": round(wall_s, 2),
    }


def timed_run(scenario: Scenario, scheduler: str) -> tuple[RunSummary, float]:
    started = time.perf_counter()
    summary = build_laboratory(scenario, scheduler=scheduler).run()
    return summary, time.perf_counter() - started


def table(rows: list[dict[str, Any]]) -> str:
    columns = list(rows[0])
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(str(r[c]) for c in columns) + " |" for r in rows]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the scripted BioFlow-X demonstration.")
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--checkpoints", type=float, nargs="*", default=[30.0, 60.0, 250.0, 905.0, 1200.0],
                        help="simulation times (min) at which to pause and print the twin")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--telemetry-out", type=Path, default=TELEMETRY,
                        help="JSON Lines file for the main run's recorded events")
    parser.add_argument("--twin-at", type=float, default=915.0,
                        help="simulation time of the twin snapshot (default: during the incubator failure)")
    parser.add_argument("--twin-out", type=Path, default=TWIN, help="PNG for the twin snapshot ('' to skip)")
    args = parser.parse_args(argv)

    try:
        scenario = load_scenario(args.scenario)
        # ------------------------------------------------------------ 1. load
        banner(f"1. Load the laboratory: {scenario.name} (seed {scenario.seed})")
        lab = build_laboratory(scenario, telemetry=TelemetryLevel.STANDARD)
        kinds = Counter(str(item.kind) for item in lab.state.equipment.values())
        print("Equipment:  " + ", ".join(f"{v} x {k}" for k, v in sorted(kinds.items())))
        if lab.layout is not None:
            grid = lab.layout.map
            print(f"Floor plan: {grid.width} x {grid.height} cells, A* routing with cell reservations")
        for spec in scenario.experiments:
            print(f"Experiment: {spec.experiment_id}  {spec.plates:>3} plates  {spec.protocol:<20} "
                  f"priority {spec.priority}  arrives t={spec.submit_at_min:g}  deadline t={spec.deadline_min}")
        for fault in scenario.faults:
            print(f"Scheduled fault (hidden from the controller): {fault.fault_type} on {fault.equipment_id} "
                  f"at t={fault.start_min:g} for {fault.duration_min:g} min")

        # ------------------------------------------------------- 2. run live
        banner(f"2. Run with the '{scenario.scheduler}' scheduler, pausing to inspect the twin")
        for checkpoint in sorted(args.checkpoints):
            lab.engine.run(until=checkpoint)
            print(snapshot(lab))
        started = time.perf_counter()
        summary = lab.run()
        wall = time.perf_counter() - started
        assert lab.recorder is not None
        records = lab.recorder.write_jsonl(args.telemetry_out)
        print(f"Telemetry: {records} events recorded -> {args.telemetry_out}")

        # ------------------------------------------- 3. faults and recovery
        banner("3. Faults: injected (ground truth) vs detected (symptoms) vs recovered")
        story = fault_story(lab)
        print("\n".join(story) if story else "no faults in this scenario")
        false_alarms = summary.faults.false_positives if summary.faults else ()
        print(f"False alarms: {len(false_alarms)}")

        # ----------------------------------------------------------- 4. results
        banner("4. Results")
        print(summary.format())

        # -------------------------------------------------------- 5. compare
        banner("5. Same workload, every scheduler (and without faults)")
        rows = []
        for name in SCHEDULERS:
            run_summary, run_wall = timed_run(scenario, name)
            rows.append(row(name, run_summary, run_wall))
        clean_summary, clean_wall = timed_run(replace(scenario, faults=()), scenario.scheduler)
        rows.append(row(f"{scenario.scheduler}, no faults", clean_summary, clean_wall))
        comparison = table(rows)
        print(comparison)

        # ------------------------------------------------- 6. twin snapshot
        if str(args.twin_out) not in ("", "."):
            banner("6. Digital-twin snapshot (the dashboard's own drawing)")
            saved = save_twin_snapshot(args.scenario, args.twin_at, args.twin_out)
            print(f"Twin at t={args.twin_at:g} min -> {saved}" if saved else "no floor plan to draw")
    except BioFlowError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        f"# Final demonstration: {scenario.name}\n\n"
        f"Generated by `python simulation/final_demo.py` (scenario `{args.scenario.as_posix()}`, "
        f"seed {scenario.seed}). Deterministic: re-running prints the same numbers except wall time.\n\n"
        f"## Faults: injected vs detected vs recovered\n\n```\n" + "\n".join(story) + "\n```\n\n"
        f"## Run summary ({summary.scheduler})\n\n```\n{summary.format()}\n```\n\n"
        f"## Scheduler comparison on the identical workload\n\n{comparison}\n\n"
        "`plates_affected` counts plates exposed to the failed equipment. Fixed-rule policies send each "
        "plate to the *first* free incubator, which concentrates plates in one unit; the cost policy's "
        "idle-time term spreads them, so one incubator failure touches fewer plates.\n\n"
        "One scenario and one seed: this shows what happened on this workload, not which policy is "
        "better in general. See `results/reports/benchmark_report.md` for the multi-seed benchmark.\n",
        encoding="utf-8",
    )
    print(f"\nMain run took {wall:.2f} s wall-clock. Report written to {args.report}")
    return 2 if summary.stalled else 0


if __name__ == "__main__":
    sys.exit(main())
