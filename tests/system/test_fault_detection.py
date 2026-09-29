"""System tests: fault detection on complete scenarios."""

from pathlib import Path

import pytest

from bioflow.scenario import build_laboratory, load_scenario

ROOT = Path(__file__).parents[2]
SCENARIOS = ROOT / "simulation" / "scenarios"


@pytest.fixture(autouse=True)
def run_from_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)


@pytest.mark.parametrize("scenario", ["basic_demo", "robot_congestion"])
def test_no_false_alarms_on_a_healthy_lab(scenario: str) -> None:
    """Congestion, waiting and deadlock resolution are normal operation, not faults."""
    lab = build_laboratory(load_scenario(SCENARIOS / f"{scenario}.yaml"))
    lab.run()

    assert lab.detector.detections == []


def test_injected_faults_are_detected_and_correctly_classified() -> None:
    lab = build_laboratory(load_scenario(SCENARIOS / "multiple_failures.yaml"))
    summary = lab.run()

    report = summary.faults
    assert report is not None
    assert report.misclassified == 0
    assert report.correct >= 8
    assert report.mean_latency_min is not None and report.mean_latency_min < 30
