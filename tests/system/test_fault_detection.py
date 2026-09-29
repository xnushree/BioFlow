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


def test_ten_faults_detected_recovered_and_all_work_completed() -> None:
    """Scenario 4 (multiple failures): one fault of every type, handled without human intervention."""
    lab = build_laboratory(load_scenario(SCENARIOS / "multiple_failures.yaml"))
    summary = lab.run()

    assert summary.tasks_completed == summary.tasks_total
    detection, recovery = summary.faults, summary.recovery
    assert detection is not None and recovery is not None
    assert (detection.correct, detection.misclassified, len(detection.missed)) == (10, 0, 0)
    assert detection.false_positives == ()
    assert recovery.completed == recovery.recoveries and recovery.unrecoverable == 0
