"""Equipment parameters ("specs") and loading them from YAML.

Specs are frozen dataclasses that validate themselves. The loader adds
file-level checks on top: missing sections, misspelt keys, wrong types.
Unknown keys are rejected on purpose: a typo such as ``capcity`` should fail
loudly, not silently fall back to a default.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Generic, TypeVar

import yaml

from bioflow.core.exceptions import ConfigurationError, ValidationError
from bioflow.core.validation import require
from bioflow.domain import CultureConditions
from bioflow.domain.conditions import DEFAULT_CO2_PCT, DEFAULT_TEMPERATURE_C


def _require_positive(value: float, name: str) -> None:
    require(value > 0, f"{name} must be positive, got {value}")


def _require_capacity(value: int) -> None:
    require(isinstance(value, int) and value >= 1, f"capacity must be an integer >= 1, got {value}")


@dataclass(frozen=True)
class RobotSpec:
    pick_time_min: float
    place_time_min: float

    def __post_init__(self) -> None:
        _require_positive(self.pick_time_min, "pick_time_min")
        _require_positive(self.place_time_min, "place_time_min")


@dataclass(frozen=True)
class IncubatorSpec:
    capacity: int
    temperature_c: float = DEFAULT_TEMPERATURE_C
    co2_pct: float = DEFAULT_CO2_PCT

    def __post_init__(self) -> None:
        _require_capacity(self.capacity)
        CultureConditions(temperature_c=self.temperature_c, co2_pct=self.co2_pct)  # validates

    @property
    def setpoint(self) -> CultureConditions:
        return CultureConditions(temperature_c=self.temperature_c, co2_pct=self.co2_pct)


@dataclass(frozen=True)
class StationSpec:
    """A single-plate processing station (media exchange or imaging)."""

    process_time_min: float

    def __post_init__(self) -> None:
        _require_positive(self.process_time_min, "process_time_min")


@dataclass(frozen=True)
class ContainerSpec:
    """Passive plate storage (storage area or waste station)."""

    capacity: int

    def __post_init__(self) -> None:
        _require_capacity(self.capacity)


SpecT = TypeVar("SpecT")


@dataclass(frozen=True)
class EquipmentGroup(Generic[SpecT]):
    """``count`` identical units sharing one spec."""

    count: int
    spec: SpecT


@dataclass(frozen=True)
class EquipmentConfig:
    robots: EquipmentGroup[RobotSpec]
    incubators: EquipmentGroup[IncubatorSpec]
    media_stations: EquipmentGroup[StationSpec]
    imaging_stations: EquipmentGroup[StationSpec]
    storage: EquipmentGroup[ContainerSpec]
    waste_stations: EquipmentGroup[ContainerSpec]


# section name -> (spec class, minimum count). A lab cannot run without
# robots, incubators or a storage area; the other stations are optional.
_SECTIONS: dict[str, tuple[type, int]] = {
    "robots": (RobotSpec, 1),
    "incubators": (IncubatorSpec, 1),
    "media_stations": (StationSpec, 0),
    "imaging_stations": (StationSpec, 0),
    "storage": (ContainerSpec, 1),
    "waste_stations": (ContainerSpec, 0),
}


def load_equipment_config(path: Path) -> EquipmentConfig:
    """Read and validate an equipment YAML file."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"Equipment config not found: {path}") from None
    except yaml.YAMLError as error:
        raise ConfigurationError(f"{path}: invalid YAML: {error}") from error
    try:
        return parse_equipment_config(data)
    except ConfigurationError as error:
        raise ConfigurationError(f"{path}: {error}") from error


def parse_equipment_config(data: Any) -> EquipmentConfig:
    """Validate already-loaded config data (a mapping of section -> settings)."""
    if not isinstance(data, Mapping):
        raise ConfigurationError("equipment config must be a mapping of sections")
    unknown = set(data) - set(_SECTIONS)
    if unknown:
        raise ConfigurationError(
            f"unknown section(s) {sorted(unknown)}; expected {sorted(_SECTIONS)}"
        )
    groups = {
        name: _parse_group(name, data.get(name), spec_cls, min_count)
        for name, (spec_cls, min_count) in _SECTIONS.items()
    }
    return EquipmentConfig(**groups)


def _parse_group(section: str, raw: Any, spec_cls: type, min_count: int) -> EquipmentGroup[Any]:
    if raw is None:
        raise ConfigurationError(f"missing section '{section}'")
    if not isinstance(raw, Mapping):
        raise ConfigurationError(f"{section}: expected a mapping, got {type(raw).__name__}")

    allowed = {"count"} | {f.name for f in fields(spec_cls)}
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigurationError(
            f"{section}: unknown key(s) {sorted(unknown)}; expected {sorted(allowed)}"
        )

    count = raw.get("count")
    if not _is_int(count) or count < min_count:
        raise ConfigurationError(f"{section}: count must be an integer >= {min_count}, got {count!r}")

    settings = {key: value for key, value in raw.items() if key != "count"}
    for key, value in settings.items():
        if not _is_number(value):
            raise ConfigurationError(f"{section}.{key}: expected a number, got {value!r}")
    try:
        spec = spec_cls(**settings)
    except TypeError as error:  # a required setting is missing
        raise ConfigurationError(f"{section}: {error}") from error
    except ValidationError as error:
        raise ConfigurationError(f"{section}: {error}") from error
    return EquipmentGroup(count=count, spec=spec)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
