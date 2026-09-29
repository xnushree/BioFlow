"""Train and evaluate the optional anomaly detector against the rule-based fault detector.

Usage (from the project root):
    python simulation/anomaly_detection.py

Train: healthy runs of benchmark workload B. Test: new seeds of workload B with injected
faults, including *subtle* ones deliberately below the rule thresholds (1.5x slower drive,
+0.4 degC or +0.4 % CO2 drift). Also measures false alarms on held-out healthy runs.
Writes results/reports/anomaly_report.md.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from bioflow.analytics.anomaly_detection import (
    AnomalyModel,
    TelemetryFeatureCollector,
    evaluate,
    label_rows,
)
from bioflow.analytics.performance import load_benchmark_plan
from bioflow.faults.diagnostics import evaluate_detection
from bioflow.faults.fault import FaultSpec, FaultType, Severity
from bioflow.scenario import build_laboratory
from bioflow.workload import WorkloadSpec, equipment_ids, generate_scenario
from bioflow.equipment.config import load_equipment_config
from bioflow.domain import EquipmentKind

WINDOW_MIN = 60.0


@dataclass(frozen=True)
class FaultTemplate:
    label: str
    fault_type: FaultType
    kind: EquipmentKind
    magnitude: float | None
    subtle: bool  # below the rule-based detector's thresholds by design


TEMPLATES = (
    FaultTemplate("slow drive 1.5x (subtle)", FaultType.ROBOT_TIMEOUT, EquipmentKind.ROBOT, 1.5, True),
    FaultTemplate("temperature +0.4 degC (subtle)", FaultType.TEMPERATURE_EXCURSION, EquipmentKind.INCUBATOR, 0.4, True),
    FaultTemplate("CO2 +0.4 % (subtle)", FaultType.CO2_EXCURSION, EquipmentKind.INCUBATOR, 0.4, True),
    FaultTemplate("slow drive 4x", FaultType.ROBOT_TIMEOUT, EquipmentKind.ROBOT, 4.0, False),
    FaultTemplate("temperature +2 degC", FaultType.TEMPERATURE_EXCURSION, EquipmentKind.INCUBATOR, 2.0, False),
)


def run_with_features(spec: WorkloadSpec, seed: int, faults: tuple[FaultSpec, ...] = ()) -> tuple[Any, list]:
    scenario = replace(generate_scenario(spec, seed), faults=faults)
    lab = build_laboratory(scenario)
    nominal = lab.layout.map.cell_size_m / lab.layout.robot_speed_m_per_min if lab.layout else None
    collector = TelemetryFeatureCollector(lab.engine.bus, {e: q.kind for e, q in lab.state.equipment.items()},
                                          nominal, WINDOW_MIN)
    summary = lab.run()
    return lab, collector.rows(until=summary.makespan), summary


def planned_faults(spec: WorkloadSpec, seed: int) -> tuple[FaultSpec, ...]:
    """One fault per template, each on a different unit, at random times inside the busy period."""
    rng = random.Random(seed)
    ids = equipment_ids(load_equipment_config(spec.equipment_config, spec.equipment_overrides))
    used: set[str] = set()
    faults = []
    for template in TEMPLATES:
        target = rng.choice([eid for eid in ids[template.kind] if eid not in used] or ids[template.kind])
        used.add(target)
        faults.append(FaultSpec(template.fault_type, target, round(rng.uniform(200, 1200), 1),
                                round(rng.uniform(180, 300), 1), Severity.MEDIUM, template.magnitude,
                                {"template": template.label}))
    return tuple(faults)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate anomaly detection on simulated telemetry.")
    parser.add_argument("--workload", default="B")
    parser.add_argument("--train-seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5, 6])
    parser.add_argument("--healthy-test-seeds", type=int, nargs="+", default=[21, 22])
    parser.add_argument("--faulty-test-seeds", type=int, nargs="+", default=[11, 12, 13, 14, 15, 16])
    args = parser.parse_args(argv)
    logging.getLogger("bioflow").setLevel(logging.ERROR)
    started = time.perf_counter()

    spec = replace(load_benchmark_plan(Path("configs/benchmarks.yaml")).workloads[args.workload], faults=None)
    healthy = [row for seed in args.train_seeds for row in run_with_features(spec, seed)[1]]
    model = AnomalyModel(false_alarm_rate=0.01).fit(healthy)
    forest_only = AnomalyModel(false_alarm_rate=0.01, guard_degenerate=False).fit(healthy)

    holdout = [row for seed in args.healthy_test_seeds for row in run_with_features(spec, seed)[1]]
    holdout_scored = model.score(holdout)
    holdout_rate = sum(r["flagged"] for r in holdout_scored) / len(holdout_scored)
    forest_holdout = forest_only.score(holdout)
    forest_holdout_rate = sum(r["flagged"] for r in forest_holdout) / len(forest_holdout)

    per_fault: list[dict[str, Any]] = []
    all_scored: list[dict[str, Any]] = []
    for seed in args.faulty_test_seeds:
        faults = planned_faults(spec, seed)
        lab, rows, summary = run_with_features(spec, seed, faults)
        labelled = label_rows(rows, lab.injector.faults, WINDOW_MIN)
        scored = model.score(labelled)
        forest_scored = forest_only.score(labelled)
        all_scored += scored
        report = evaluate_detection(lab.injector.faults, lab.detector.detections)
        rule_hits = {m.fault_id for m in report.matches}
        for fault in lab.injector.faults:
            windows = [r for r in scored if r["equipment_id"] == fault.equipment_id and r["anomalous"]
                       and str(fault.fault_type) in r["fault_types"]]
            forest_windows = [r for r in forest_scored if r["equipment_id"] == fault.equipment_id and r["anomalous"]
                              and str(fault.fault_type) in r["fault_types"]]
            per_fault.append({"template": fault.spec.metadata["template"], "rules": fault.fault_id in rule_hits,
                              "ml": any(r["flagged"] for r in windows),
                              "forest": any(r["flagged"] for r in forest_windows), "exercised": bool(windows)})

    lines = [
        "# Anomaly detection on simulated equipment telemetry", "",
        f"Isolation Forest per equipment kind, trained on {len(healthy)} healthy {WINDOW_MIN:g}-minute windows "
        f"(workload {args.workload}, seeds {args.train_seeds}); alarm threshold at the 99th percentile of healthy "
        f"scores. {time.perf_counter() - started:.0f} s.", "",
        f"**False alarms on held-out healthy runs** (seeds {args.healthy_test_seeds}): "
        f"{holdout_rate:.1%} of {len(holdout_scored)} windows with the guard, {forest_holdout_rate:.1%} forest only "
        "(target 1%).", "",
        "Features that never varied in healthy data (the Isolation Forest cannot use them, so they get an "
        f"extrapolation guard): {model.degenerate_features}.", "",
        "## Which faults were caught", "",
        f"Test runs: seeds {args.faulty_test_seeds}, one of each fault per run. *Subtle* faults are deliberately "
        "below the rule-based thresholds (slow drive alarms at 2x, excursions at +-0.5).", "",
        "| Fault | Runs | Caught by rules | Isolation Forest only | Forest + degenerate-feature guard |",
        "|---|---|---|---|---|",
    ]
    for template in TEMPLATES:
        rows = [r for r in per_fault if r["template"] == template.label]
        lines.append(f"| {template.label} | {len(rows)} | {sum(r['rules'] for r in rows)}/{len(rows)} | "
                     f"{sum(r['forest'] for r in rows)}/{len(rows)} | {sum(r['ml'] for r in rows)}/{len(rows)} |")
    lines += ["", "## Window-level scores (faulted runs)", "",
              "| Kind | Windows | Anomalous | Flagged | Precision | Recall | ROC AUC |", "|---|---|---|---|---|---|---|"]
    for result in evaluate(all_scored):
        fmt = lambda v: "-" if v is None else f"{v:.2f}"  # noqa: E731
        lines.append(f"| {result.kind} | {result.windows} | {result.anomalous} | {result.flagged} | "
                     f"{fmt(result.precision)} | {fmt(result.recall)} | {fmt(result.auc)} |")
    lines += ["", "## Caveats", "",
              "- The simulated drive has no noise, so a healthy robot's drive-time ratio is exactly 1.0 and any "
              "slowdown is trivially visible once guarded. A real drive varies, and small slowdowns would be harder "
              "to separate from noise.",
              "- Faults are labelled per window by ground truth; a fault that begins late in a window, or is not "
              "exercised (an idle robot), contributes windows the model could not possibly flag.",
              "- This is anomaly detection on *simulated* telemetry. It says nothing about predicting failures of "
              "real equipment.", ""]
    path = Path("results/reports/anomaly_report.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
