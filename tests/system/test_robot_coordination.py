"""System tests: many robots on the real lab layout, checked for collisions end to end."""

from pathlib import Path

import pytest
import yaml

from bioflow.core.events import Event
from bioflow.core.exceptions import ConfigurationError
from bioflow.equipment.config import load_equipment_config
from bioflow.robotics.motion import GridMotion, MotionEvent
from bioflow.scenario import build_laboratory, load_scenario, parse_scenario

ROOT = Path(__file__).parents[2]
CONGESTION = ROOT / "simulation" / "scenarios" / "robot_congestion.yaml"


@pytest.fixture(autouse=True)
def run_from_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)


def test_congested_lab_completes_without_any_collision() -> None:
    lab = build_laboratory(load_scenario(CONGESTION))
    assert isinstance(lab.motion, GridMotion)
    positions = lab.motion.positions()
    worst_sharing = 1

    def on_move(event: Event) -> None:
        nonlocal worst_sharing
        positions[event.source] = event.payload["to_cell"]
        cells = list(positions.values())
        worst_sharing = max(worst_sharing, len(cells) - len(set(cells)) + 1)

    lab.engine.bus.subscribe(MotionEvent.ROBOT_MOVED, on_move)
    summary = lab.run()

    assert summary.tasks_completed == summary.tasks_total == 30 * 9 + 10 * 5
    assert worst_sharing == 1, "two robots occupied the same cell"
    assert summary.motion is not None and summary.motion.deadlocks > 0  # the scenario really is congested
    assert "deadlocks resolved" in summary.format()


def test_every_robot_parks_when_the_work_is_done() -> None:
    lab = build_laboratory(load_scenario(CONGESTION))
    lab.run()

    final_cells = set(lab.motion.positions().values())  # type: ignore[union-attr]
    layout = yaml.safe_load((ROOT / "configs" / "laboratory.yaml").read_text(encoding="utf-8"))
    assert final_cells == {tuple(cell) for cell in layout["parking"][:4]}


def test_congested_runs_are_deterministic() -> None:
    scenario = load_scenario(CONGESTION)

    assert build_laboratory(scenario).run() == build_laboratory(scenario).run()


def test_equipment_overrides_change_only_named_settings() -> None:
    config = load_equipment_config(ROOT / "configs" / "equipment.yaml", {"robots": {"count": 4}})

    assert config.robots.count == 4
    assert config.robots.spec.pick_time_min == 0.5  # untouched setting kept from the file


def test_invalid_override_is_reported() -> None:
    with pytest.raises(ConfigurationError, match="robots: count must be an integer"):
        load_equipment_config(ROOT / "configs" / "equipment.yaml", {"robots": {"count": "four"}})


def test_too_many_robots_for_the_parking_area() -> None:
    data = yaml.safe_load(CONGESTION.read_text(encoding="utf-8"))
    data["equipment_overrides"]["robots"]["count"] = 7  # the layout has 6 parking cells

    with pytest.raises(ConfigurationError, match="no free parking cell for ROBOT_07"):
        build_laboratory(parse_scenario(data))
