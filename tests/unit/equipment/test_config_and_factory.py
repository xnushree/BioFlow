"""Tests for equipment config loading and the equipment factory."""

import copy
from pathlib import Path
from typing import Any

import pytest

from bioflow.core.exceptions import ConfigurationError
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import EquipmentKind
from bioflow.equipment import Incubator, Robot
from bioflow.equipment.config import load_equipment_config, parse_equipment_config
from bioflow.equipment.factory import build_equipment
from bioflow.robotics.motion import TimedMotion
from bioflow.robotics.travel import ConstantTravelTime

REPO_CONFIG = Path(__file__).parents[3] / "configs" / "equipment.yaml"

VALID: dict[str, Any] = {
    "robots": {"count": 2, "pick_time_min": 0.5, "place_time_min": 0.5},
    "incubators": {"count": 2, "capacity": 120},
    "media_stations": {"count": 1, "process_time_min": 15},
    "imaging_stations": {"count": 1, "process_time_min": 10},
    "storage": {"count": 1, "capacity": 500},
    "waste_stations": {"count": 0, "capacity": 10},
}


def with_change(section: str, **changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(VALID)
    data[section].update(changes)
    return data


def test_repository_config_file_loads() -> None:
    config = load_equipment_config(REPO_CONFIG)

    assert config.robots.count == 2
    assert config.incubators.spec.capacity == 120
    assert config.imaging_stations.spec.process_time_min == 10.0


def test_valid_data_parses() -> None:
    config = parse_equipment_config(VALID)

    assert config.media_stations.spec.process_time_min == 15
    assert config.incubators.spec.temperature_c == 37.0  # default applied


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({k: v for k, v in VALID.items() if k != "robots"}, "missing section 'robots'"),
        ({**VALID, "lasers": {"count": 1}}, "unknown section"),
        (with_change("incubators", capcity=5), r"unknown key\(s\) \['capcity'\]"),
        (with_change("robots", count=0), "robots: count must be an integer >= 1"),
        (with_change("robots", count=1.5), "count must be an integer"),
        (with_change("media_stations", process_time_min="fast"), "expected a number"),
        (with_change("media_stations", process_time_min=-5), "must be positive"),
        (with_change("incubators", capacity=0), "capacity must be an integer >= 1"),
        (with_change("incubators", co2_pct=150), "co2_pct"),
        ({**VALID, "storage": {"count": 1}}, "storage:.*capacity"),  # required setting missing
        ([1, 2, 3], "must be a mapping"),
    ],
)
def test_invalid_config_gives_useful_error(data: Any, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        parse_equipment_config(data)


def test_missing_file_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_equipment_config(tmp_path / "nope.yaml")


def test_malformed_yaml_is_a_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("robots: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="invalid YAML"):
        load_equipment_config(path)


def test_error_message_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("robots: {count: 0}\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="bad.yaml"):
        load_equipment_config(path)


def test_factory_builds_all_equipment_with_standard_ids(engine: SimulationEngine) -> None:
    equipment = build_equipment(parse_equipment_config(VALID), engine, TimedMotion(engine, ConstantTravelTime(1)))

    assert list(equipment) == [
        "STORAGE_01", "INCUBATOR_01", "INCUBATOR_02", "MEDIA_01", "IMAGING_01", "ROBOT_01", "ROBOT_02",
    ]
    assert equipment["MEDIA_01"].kind is EquipmentKind.MEDIA_STATION
    assert isinstance(equipment["INCUBATOR_02"], Incubator)
    robot = equipment["ROBOT_01"]
    assert isinstance(robot, Robot) and robot.location_id == "STORAGE_01"
