"""Tests for benchmark charts and the analysis scripts (tiny workloads so they stay fast)."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from bioflow.analytics.performance import aggregate
from bioflow.analytics.plots import SCHEDULER_COLORS, save_metric_chart, save_overview

ROOT = Path(__file__).parents[3]


def rows() -> list[dict]:
    data = []
    for scheduler, base in (("fifo", 100.0), ("cost", 90.0)):
        for workload in ("A", "B"):
            for seed in (1, 2):
                data.append({"workload": workload, "scheduler": scheduler, "seed": seed,
                             "makespan_min": base + seed, "deadline_miss_rate": 0.1 * seed})
    return data


def test_charts_are_rendered(tmp_path: Path) -> None:
    summary = aggregate(rows(), metrics=["makespan_min", "deadline_miss_rate"])

    single = save_metric_chart(summary, "makespan_min", tmp_path / "one.png", ["A", "B"], ["fifo", "cost"])
    overview = save_overview(summary, tmp_path / "all.png", ["A", "B"], ["fifo", "cost"],
                             ["makespan_min", "deadline_miss_rate"])

    for path in (single, overview):
        assert path.read_bytes().startswith(b"\x89PNG") and path.stat().st_size > 5_000


def test_every_scheduler_has_a_fixed_colour() -> None:
    from bioflow.scheduling.registry import SCHEDULERS

    assert set(SCHEDULERS) <= set(SCHEDULER_COLORS)
    assert len(set(SCHEDULER_COLORS.values())) == len(SCHEDULER_COLORS)


@pytest.fixture
def tiny_benchmark(tmp_path: Path) -> Path:
    config = yaml.safe_load((ROOT / "configs" / "benchmarks.yaml").read_text(encoding="utf-8"))
    config["seeds"] = [1]
    config["schedulers"] = ["fifo", "cost"]
    config["workloads"] = {"A": {**config["workloads"]["A"], "plates": 3}}
    path = tmp_path / "tiny.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_benchmark_script_end_to_end(tiny_benchmark: Path, tmp_path: Path) -> None:
    out = tmp_path / "results"
    result = subprocess.run(
        [sys.executable, "simulation/benchmark.py", "--config", str(tiny_benchmark), "--out", str(out)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (out / "benchmarks" / "runs.csv").read_text().count("\n") == 3  # header + 2 runs
    report = (out / "reports" / "benchmark_report.md").read_text(encoding="utf-8")
    assert "Paired comparison against FIFO" in report and "Integrity:" in report
    assert (out / "plots" / "benchmark_overview.png").exists()


def test_tuning_script_refuses_overlapping_seeds() -> None:
    result = subprocess.run(
        [sys.executable, "simulation/tune_cost_weights.py", "--train-seeds", "1", "2", "--test-seeds", "2", "3"],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )

    assert result.returncode != 0 and "must not overlap" in result.stderr
