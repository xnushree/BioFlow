"""Tests for loading laboratory layouts from YAML, and the render command."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from bioflow.core.exceptions import ConfigurationError
from bioflow.core.simulation import SimulationEngine
from bioflow.equipment.config import load_equipment_config
from bioflow.equipment.factory import build_equipment
from bioflow.robotics.__main__ import main
from bioflow.robotics.layout import load_layout, parse_layout

ROOT = Path(__file__).parents[3]
LAYOUT = ROOT / "configs" / "laboratory.yaml"


@pytest.fixture
def layout_data() -> dict[str, Any]:
    return yaml.safe_load(LAYOUT.read_text(encoding="utf-8"))


def test_repository_layout_loads() -> None:
    layout = load_layout(LAYOUT)

    assert (layout.map.width, layout.map.height) == (30, 20)
    assert layout.robot_speed_m_per_min == 12.0
    assert layout.map.access_point("STORAGE_01") == (3, 9)
    assert layout.map.step_cost((3, 2)) == 1.5  # incubator clearance zone


def test_repository_layout_places_all_configured_equipment() -> None:
    equipment = build_equipment(load_equipment_config(ROOT / "configs" / "equipment.yaml"), SimulationEngine())
    non_robots = [eid for eid in equipment if not eid.startswith("ROBOT")]

    load_layout(LAYOUT).map.check_covers(non_robots)


def test_repository_layout_has_room_to_scale_up() -> None:
    ids = load_layout(LAYOUT).map.equipment_ids

    assert {"INCUBATOR_04", "MEDIA_02", "IMAGING_02"} <= set(ids)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.pop("movement"), "missing required key 'movement'"),
        (lambda d: d["grid"].update(widht=5), "unknown key 'widht' \\(did you mean 'width'"),
        (lambda d: d["grid"].update(width=30.5), "grid.width: expected an integer"),
        (lambda d: d["movement"].update(robot_speed_m_per_min=0), "must be positive"),
        (lambda d: d["equipment"]["STORAGE_01"].update(access=[3]), "access: expected \\[x, y\\]"),
        (lambda d: d["equipment"]["STORAGE_01"].pop("width"), "missing required key 'width'"),
        (lambda d: d.update(blocked={"x": 1}), "blocked: expected a list"),
        (lambda d: d["zones"][0].update(speed=2), "zones\\[0\\]: unknown key 'speed'"),
        (lambda d: d["blocked"].append({"x": 2, "y": 8, "width": 2, "height": 2}), "overlaps"),
    ],
)
def test_invalid_layouts(layout_data: dict[str, Any], change: Any, message: str) -> None:
    data = copy.deepcopy(layout_data)
    change(data)

    with pytest.raises(ConfigurationError, match=message):
        parse_layout(data)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_layout(tmp_path / "none.yaml")


def test_render_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(LAYOUT)]) == 0
    output = capsys.readouterr().out
    assert "30x20 cells (15 m x 10 m)" in output
    assert "SSS+" in output


def test_render_command_reports_invalid_layout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("grid: {}\n", encoding="utf-8")

    assert main([str(bad)]) == 1
    assert "missing required key" in capsys.readouterr().err
