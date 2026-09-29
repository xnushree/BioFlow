"""System tests: complete scenarios from YAML files, as a user would run them."""

import copy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from bioflow.core.exceptions import ConfigurationError
from bioflow.scenario import build_laboratory, load_scenario, parse_scenario, run_scenario

ROOT = Path(__file__).parents[2]
BASIC_DEMO = ROOT / "simulation" / "scenarios" / "basic_demo.yaml"


@pytest.fixture(autouse=True)
def run_from_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)  # scenario paths are relative to the project root


@pytest.fixture
def demo_data() -> dict[str, Any]:
    return yaml.safe_load(BASIC_DEMO.read_text(encoding="utf-8"))


def test_basic_demo_completes_every_task() -> None:
    summary = run_scenario(load_scenario(BASIC_DEMO))

    assert summary.tasks_completed == summary.tasks_total == 10 * 5 + 4 * 7
    assert not summary.stalled
    assert {r.status for r in summary.experiments} == {"COMPLETED"}


def test_runs_are_deterministic() -> None:
    scenario = load_scenario(BASIC_DEMO)

    assert run_scenario(scenario) == run_scenario(scenario)


def test_stall_is_detected_and_reported(demo_data: dict[str, Any], tmp_path: Path) -> None:
    """No imaging station: IMAGE tasks can never run, and the summary must say so."""
    equipment = yaml.safe_load((ROOT / "configs" / "equipment.yaml").read_text(encoding="utf-8"))
    equipment["imaging_stations"]["count"] = 0
    config_path = tmp_path / "no_imaging.yaml"
    config_path.write_text(yaml.safe_dump(equipment), encoding="utf-8")
    demo_data["equipment_config"] = str(config_path)

    summary = run_scenario(parse_scenario(demo_data))

    assert summary.stalled
    assert "EXP001-P001-S04" in summary.stalled_tasks  # the first IMAGE step
    assert "STALLED" in summary.format()


def test_run_until_is_not_a_stall() -> None:
    summary = run_scenario(load_scenario(BASIC_DEMO), until=100)

    assert summary.end_time == 100
    assert not summary.stalled
    assert summary.tasks_completed < summary.tasks_total


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.pop("experiments"), "missing required key 'experiments'"),
        (lambda d: d.update(schedular="fifo"), "unknown key 'schedular' \\(did you mean 'scheduler'"),
        (lambda d: d.update(experiments=[]), "non-empty list"),
        (lambda d: d["experiments"][0].update(plates="ten"), "experiment 1.plates: expected an integer"),
        (lambda d: d["experiments"][1].update(id="EXP001"), "duplicate ids \\['EXP001'\\]"),
        (lambda d: d.update(seed=1.5), "seed: expected an integer"),
    ],
)
def test_invalid_scenarios_are_rejected(demo_data: dict[str, Any], change: Any, message: str) -> None:
    data = copy.deepcopy(demo_data)
    change(data)

    with pytest.raises(ConfigurationError, match=message):
        parse_scenario(data)


def test_unknown_protocol_is_reported_with_suggestion(demo_data: dict[str, Any]) -> None:
    demo_data["experiments"][0]["protocol"] = "basic_experment"

    with pytest.raises(ConfigurationError, match="did you mean 'basic_experiment'"):
        run_scenario(parse_scenario(demo_data))


def test_command_line_runner() -> None:
    result = subprocess.run(
        [sys.executable, "simulation/run_simulation.py", str(BASIC_DEMO)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Tasks completed:    78/78" in result.stdout


@pytest.mark.parametrize("scheduler", ["fifo", "priority", "deadline", "cost"])
def test_every_policy_completes_the_demo_correctly(scheduler: str) -> None:
    """Policies change *order*, never correctness: every task completes and dependencies hold."""
    lab = build_laboratory(load_scenario(BASIC_DEMO), scheduler=scheduler)
    summary = lab.run()

    assert summary.scheduler == scheduler
    assert summary.tasks_completed == summary.tasks_total
    for task in lab.state.tasks:
        for dep in task.depends_on:
            assert task.started_at >= lab.state.tasks.get(dep).completed_at


def test_compare_schedulers_script() -> None:
    result = subprocess.run(
        [sys.executable, "simulation/compare_schedulers.py", str(BASIC_DEMO)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )

    assert result.returncode == 0, result.stderr
    for name in ("fifo", "priority", "deadline", "cost"):
        assert name in result.stdout
