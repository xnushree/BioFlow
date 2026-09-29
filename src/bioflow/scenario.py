"""Scenario files: which lab, which scheduler, which experiments arrive when.

Paths inside a scenario (``equipment_config``, ``protocol_dir``, ...) are
relative to the directory the simulation is run from, normally the project root.

Travel times come from either a laboratory layout (``laboratory_config``: A*
paths on the floor plan) or a fixed ``travel_time_min`` for every trip. A
scenario may set one or the other, not both.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

import yaml

from bioflow.analytics.summary import RunSummary
from bioflow.core.exceptions import BioFlowError, ConfigurationError
from bioflow.core.validation import is_int, is_number, suggest
from bioflow.domain import Experiment
from bioflow.equipment.config import load_equipment_config
from bioflow.faults.config import load_fault_config
from bioflow.faults.fault import FaultSpec, FaultType, Severity
from bioflow.laboratory import Laboratory
from bioflow.protocols import load_protocol_library
from bioflow.robotics.layout import load_layout
from bioflow.robotics.travel import ConstantTravelTime, MapTravelTime, TravelTimeModel
from bioflow.scheduling.config import SchedulingConfig, load_scheduling_config
from bioflow.scheduling.registry import create_scheduler
from bioflow.telemetry.recorder import TelemetryLevel

_TOP_REQUIRED = ("scenario", "equipment_config", "protocol_dir", "experiments")
_TOP_OPTIONAL = (
    "description", "seed", "scheduler", "scheduling_config", "travel_time_min", "laboratory_config",
    "equipment_overrides", "faults", "fault_config",
)
_EXPERIMENT_REQUIRED = ("id", "protocol", "plates")
_EXPERIMENT_OPTIONAL = ("priority", "submit_at_min", "deadline_min")
_FAULT_REQUIRED = ("type", "equipment", "at_min")
_FAULT_OPTIONAL = ("duration_min", "severity", "magnitude", "mode")
DEFAULT_SCHEDULER = "fifo"
DEFAULT_TRAVEL_TIME_MIN = 2.0


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    protocol: str
    plates: int
    priority: int = 0
    submit_at_min: float = 0.0
    deadline_min: float | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    seed: int
    scheduler: str
    equipment_config: Path
    protocol_dir: Path
    travel_time_min: float
    experiments: tuple[ExperimentSpec, ...]
    scheduling_config: Path | None = None
    laboratory_config: Path | None = None
    equipment_overrides: Mapping[str, Any] | None = None
    faults: tuple[FaultSpec, ...] = ()
    fault_config: Path | None = None


def load_scenario(path: Path) -> Scenario:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"Scenario not found: {path}") from None
    except yaml.YAMLError as error:
        raise ConfigurationError(f"{path}: invalid YAML: {error}") from error
    try:
        return parse_scenario(data)
    except ConfigurationError as error:
        raise ConfigurationError(f"{path.name}: {error}") from error


def parse_scenario(data: Any) -> Scenario:
    raw = _mapping(data, "scenario file")
    _check_keys(raw, _TOP_REQUIRED, _TOP_OPTIONAL, "scenario")
    experiments_raw = raw["experiments"]
    if not isinstance(experiments_raw, list) or not experiments_raw:
        raise ConfigurationError("experiments: expected a non-empty list")
    experiments = tuple(_parse_experiment(item, n) for n, item in enumerate(experiments_raw, start=1))
    ids = [e.experiment_id for e in experiments]
    duplicates = sorted(i for i, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ConfigurationError(f"experiments: duplicate ids {duplicates}")
    if "laboratory_config" in raw and "travel_time_min" in raw:
        raise ConfigurationError(
            "set either laboratory_config (map-based travel) or travel_time_min (fixed travel), not both"
        )

    return Scenario(
        name=_text(raw, "scenario"),
        description=str(raw.get("description", "")),
        seed=_integer(raw, "seed", default=0),
        scheduler=_text(raw, "scheduler", default=DEFAULT_SCHEDULER),
        equipment_config=Path(_text(raw, "equipment_config")),
        protocol_dir=Path(_text(raw, "protocol_dir")),
        travel_time_min=_number(raw, "travel_time_min", default=DEFAULT_TRAVEL_TIME_MIN),
        experiments=experiments,
        scheduling_config=Path(_text(raw, "scheduling_config")) if "scheduling_config" in raw else None,
        laboratory_config=Path(_text(raw, "laboratory_config")) if "laboratory_config" in raw else None,
        equipment_overrides=_mapping(raw["equipment_overrides"], "equipment_overrides")
        if "equipment_overrides" in raw else None,
        faults=tuple(_parse_fault(item, n) for n, item in enumerate(_list(raw.get("faults"), "faults"), start=1)),
        fault_config=Path(_text(raw, "fault_config")) if "fault_config" in raw else None,
    )


def _parse_fault(item: Any, number: int) -> FaultSpec:
    where = f"fault {number}"
    raw = _mapping(item, where)
    _check_keys(raw, _FAULT_REQUIRED, _FAULT_OPTIONAL, where)
    fault_type = _enum(raw, "type", FaultType, where)
    severity = _enum(raw, "severity", Severity, where) if "severity" in raw else Severity.MEDIUM
    duration = raw.get("duration_min")
    magnitude = raw.get("magnitude")
    for key, value in (("duration_min", duration), ("magnitude", magnitude)):
        if value is not None and not is_number(value):
            raise ConfigurationError(f"{where}.{key}: expected a number, got {value!r}")
    try:
        return FaultSpec(
            fault_type=fault_type,
            equipment_id=_text(raw, "equipment", where=where),
            start_min=_number(raw, "at_min", where=where),
            duration_min=duration,
            severity=severity,
            magnitude=magnitude,
            metadata={"mode": raw["mode"]} if "mode" in raw else {},
        )
    except BioFlowError as error:
        raise ConfigurationError(f"{where}: {error}") from error


def _parse_experiment(item: Any, number: int) -> ExperimentSpec:
    where = f"experiment {number}"
    raw = _mapping(item, where)
    _check_keys(raw, _EXPERIMENT_REQUIRED, _EXPERIMENT_OPTIONAL, where)
    deadline = raw.get("deadline_min")
    if deadline is not None and not is_number(deadline):
        raise ConfigurationError(f"{where}.deadline_min: expected a number, got {deadline!r}")
    return ExperimentSpec(
        experiment_id=_text(raw, "id", where=where),
        protocol=_text(raw, "protocol", where=where),
        plates=_integer(raw, "plates", where=where),
        priority=_integer(raw, "priority", default=0, where=where),
        submit_at_min=_number(raw, "submit_at_min", default=0.0, where=where),
        deadline_min=deadline,
    )


# ---------------------------------------------------------------- running
def build_laboratory(
    scenario: Scenario, scheduler: str | None = None, telemetry: TelemetryLevel | None = None
) -> Laboratory:
    """Create the lab described by ``scenario`` and schedule its experiments.

    ``scheduler`` overrides the scenario's choice, for comparing policies on the same workload.
    ``telemetry`` turns on structured event recording at that detail level.
    """
    protocols = load_protocol_library(scenario.protocol_dir)
    scheduling = (
        load_scheduling_config(scenario.scheduling_config) if scenario.scheduling_config else SchedulingConfig()
    )
    layout = load_layout(scenario.laboratory_config) if scenario.laboratory_config else None
    travel: TravelTimeModel = (
        MapTravelTime(layout.map, layout.robot_speed_m_per_min) if layout
        else ConstantTravelTime(scenario.travel_time_min)
    )
    lab = Laboratory(
        equipment_config=load_equipment_config(scenario.equipment_config, scenario.equipment_overrides),
        scheduler=create_scheduler(scheduler or scenario.scheduler, scheduling),
        travel=travel,
        seed=scenario.seed,
        layout=layout,
        fault_config=load_fault_config(scenario.fault_config) if scenario.fault_config else None,
        telemetry=telemetry,
    )
    for spec in scenario.experiments:
        if spec.protocol not in protocols:
            raise ConfigurationError(
                f"{spec.experiment_id}: unknown protocol {spec.protocol!r}{suggest(spec.protocol, protocols)}"
            )
        try:
            experiment = Experiment(
                experiment_id=spec.experiment_id,
                protocol=protocols[spec.protocol],
                plate_count=spec.plates,
                submitted_at=spec.submit_at_min,
                priority=spec.priority,
                deadline=spec.deadline_min,
            )
        except BioFlowError as error:
            raise ConfigurationError(f"{spec.experiment_id}: {error}") from error
        lab.schedule_experiment(experiment)
    for fault in scenario.faults:
        try:
            lab.schedule_fault(fault)
        except BioFlowError as error:
            raise ConfigurationError(f"fault on {fault.equipment_id}: {error}") from error
    return lab


def run_scenario(scenario: Scenario, scheduler: str | None = None, until: float | None = None) -> RunSummary:
    return build_laboratory(scenario, scheduler).run(until=until)


# ---------------------------------------------------------------- helpers
def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{where}: expected a mapping")
    return value


def _check_keys(raw: Mapping[str, Any], required: tuple[str, ...], optional: tuple[str, ...], where: str) -> None:
    allowed = required + optional
    for key in raw:
        if key not in allowed:
            raise ConfigurationError(f"{where}: unknown key '{key}'{suggest(str(key), allowed)}")
    for key in required:
        if key not in raw:
            raise ConfigurationError(f"{where}: missing required key '{key}'")


def _text(raw: Mapping[str, Any], key: str, default: str | None = None, where: str = "scenario") -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{where}.{key}: expected non-empty text, got {value!r}")
    return value


def _integer(raw: Mapping[str, Any], key: str, default: int | None = None, where: str = "scenario") -> int:
    value = raw.get(key, default)
    if not is_int(value):
        raise ConfigurationError(f"{where}.{key}: expected an integer, got {value!r}")
    return value


def _number(raw: Mapping[str, Any], key: str, default: float | None = None, where: str = "scenario") -> float:
    value = raw.get(key, default)
    if not is_number(value):
        raise ConfigurationError(f"{where}.{key}: expected a number, got {value!r}")
    return float(value)


def _list(value: Any, where: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigurationError(f"{where}: expected a list")
    return value


E = TypeVar("E", bound=StrEnum)


def _enum(raw: Mapping[str, Any], key: str, enum: type[E], where: str) -> E:
    value = raw[key]
    names = [member.value for member in enum]
    if value not in names:
        raise ConfigurationError(
            f"{where}.{key}: unknown value {value!r}{suggest(str(value), names)}; expected one of {', '.join(names)}"
        )
    return enum(value)
