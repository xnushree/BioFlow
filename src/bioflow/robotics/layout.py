"""Load a laboratory layout (grid, equipment positions, obstacles, zones) from YAML.

This module checks the *document* (keys, types). Geometry rules (overlaps,
bounds, reachability) are enforced by ``LabMap`` itself, so a layout built in
code is held to the same rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bioflow.core.exceptions import ConfigurationError
from bioflow.core.validation import is_int, is_number, suggest
from bioflow.robotics.map import Cell, EquipmentPlacement, LabMap, Rect, Zone

_TOP_KEYS = ("grid", "movement", "equipment", "blocked", "zones", "parking")
_RECT_KEYS = ("x", "y", "width", "height")


@dataclass(frozen=True)
class LabLayout:
    map: LabMap
    robot_speed_m_per_min: float


def load_layout(path: Path) -> LabLayout:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"Laboratory layout not found: {path}") from None
    except yaml.YAMLError as error:
        raise ConfigurationError(f"{path}: invalid YAML: {error}") from error
    try:
        return parse_layout(data)
    except ConfigurationError as error:
        raise ConfigurationError(f"{path.name}: {error}") from error


def parse_layout(data: Any) -> LabLayout:
    raw = _mapping(data, "layout")
    _keys(raw, _TOP_KEYS, ("grid", "movement", "equipment"), "layout")

    grid = _mapping(raw["grid"], "grid")
    _keys(grid, ("width", "height", "cell_size_m"), ("width", "height", "cell_size_m"), "grid")
    movement = _mapping(raw["movement"], "movement")
    _keys(movement, ("robot_speed_m_per_min",), ("robot_speed_m_per_min",), "movement")
    speed = _number(movement, "robot_speed_m_per_min", "movement")
    if speed <= 0:
        raise ConfigurationError(f"movement.robot_speed_m_per_min must be positive, got {speed}")

    equipment = _mapping(raw["equipment"], "equipment")
    placements = [_placement(eid, spec) for eid, spec in equipment.items()]
    blocked = [_rect(item, f"blocked[{i}]") for i, item in enumerate(_list(raw.get("blocked"), "blocked"))]
    zones = [_zone(item, f"zones[{i}]") for i, item in enumerate(_list(raw.get("zones"), "zones"))]
    parking = [_cell(item, f"parking[{i}]") for i, item in enumerate(_list(raw.get("parking"), "parking"))]

    lab_map = LabMap(
        width=_integer(grid, "width", "grid"),
        height=_integer(grid, "height", "grid"),
        cell_size_m=_number(grid, "cell_size_m", "grid"),
        placements=placements,
        blocked=blocked,
        zones=zones,
        parking=parking,
    )
    return LabLayout(map=lab_map, robot_speed_m_per_min=speed)


def _placement(equipment_id: str, spec: Any) -> EquipmentPlacement:
    where = f"equipment.{equipment_id}"
    raw = _mapping(spec, where)
    _keys(raw, _RECT_KEYS + ("access",), _RECT_KEYS + ("access",), where)
    return EquipmentPlacement(
        equipment_id, _rect(raw, where, extra=("access",)), _cell(raw["access"], f"{where}.access")
    )


def _cell(value: Any, where: str) -> Cell:
    if not (isinstance(value, list) and len(value) == 2 and all(is_int(v) for v in value)):
        raise ConfigurationError(f"{where}: expected [x, y], got {value!r}")
    return (value[0], value[1])


def _zone(item: Any, where: str) -> Zone:
    raw = _mapping(item, where)
    _keys(raw, _RECT_KEYS + ("name", "cost"), _RECT_KEYS + ("name", "cost"), where)
    return Zone(str(raw["name"]), _rect(raw, where, extra=("name", "cost")), _number(raw, "cost", where))


def _rect(item: Any, where: str, extra: tuple[str, ...] = ()) -> Rect:
    raw = _mapping(item, where)
    if not extra:
        _keys(raw, _RECT_KEYS, _RECT_KEYS, where)
    return Rect(*(_integer(raw, key, where) for key in _RECT_KEYS))


# ------------------------------------------------------------------ helpers
def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{where}: expected a mapping")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigurationError(f"{where}: expected a list")
    return value


def _keys(raw: Mapping[str, Any], allowed: tuple[str, ...], required: tuple[str, ...], where: str) -> None:
    for key in raw:
        if key not in allowed:
            raise ConfigurationError(f"{where}: unknown key '{key}'{suggest(str(key), allowed)}")
    for key in required:
        if key not in raw:
            raise ConfigurationError(f"{where}: missing required key '{key}'")


def _integer(raw: Mapping[str, Any], key: str, where: str) -> int:
    value = raw[key]
    if not is_int(value):
        raise ConfigurationError(f"{where}.{key}: expected an integer, got {value!r}")
    return value


def _number(raw: Mapping[str, Any], key: str, where: str) -> float:
    value = raw[key]
    if not is_number(value):
        raise ConfigurationError(f"{where}.{key}: expected a number, got {value!r}")
    return float(value)
