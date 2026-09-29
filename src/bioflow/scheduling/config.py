"""Scheduler settings (currently the cost scheduler's weights) and their YAML loader."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from bioflow.core.exceptions import ConfigurationError, ValidationError
from bioflow.core.validation import is_number, require, suggest

# Defaults found by random search (simulation/tune_cost_weights.py) on benchmark
# workload C and validated on held-out seeds and on workloads A, B and D; see
# docs/benchmarking.md. The original hand-tuned values were travel 1.0,
# switching 5.0, delay 0.05, idle 0.01, deadline 50.0.


@dataclass(frozen=True)
class CostWeights:
    travel: float = 4.3854
    switching: float = 11.1095
    delay: float = 0.0183
    idle: float = 0.003
    deadline: float = 23.9836

    def __post_init__(self) -> None:
        for f in fields(self):
            require(getattr(self, f.name) >= 0, f"weight '{f.name}' must be >= 0")


@dataclass(frozen=True)
class CostSettings:
    weights: CostWeights = field(default_factory=CostWeights)
    default_step_min: float = 15.0  # assumed duration of a step with no explicit duration
    max_critical_ratio: float = 10.0  # cap, so an already-late task does not dominate forever

    def __post_init__(self) -> None:
        require(self.default_step_min > 0, "default_step_min must be positive")
        require(self.max_critical_ratio > 0, "max_critical_ratio must be positive")


@dataclass(frozen=True)
class SchedulingConfig:
    cost: CostSettings = field(default_factory=CostSettings)


_COST_KEYS = ("weights", "default_step_min", "max_critical_ratio")
_WEIGHT_KEYS = tuple(f.name for f in fields(CostWeights))


def load_scheduling_config(path: Path) -> SchedulingConfig:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"Scheduling config not found: {path}") from None
    except yaml.YAMLError as error:
        raise ConfigurationError(f"{path}: invalid YAML: {error}") from error
    try:
        return parse_scheduling_config(data)
    except ConfigurationError as error:
        raise ConfigurationError(f"{path.name}: {error}") from error


def parse_scheduling_config(data: Any) -> SchedulingConfig:
    raw = _mapping(data or {}, "scheduling config")
    _only(raw, ("cost",), "scheduling config")
    cost = _mapping(raw.get("cost") or {}, "cost")
    _only(cost, _COST_KEYS, "cost")
    weights = _mapping(cost.get("weights") or {}, "cost.weights")
    _only(weights, _WEIGHT_KEYS, "cost.weights")
    _numbers(weights, "cost.weights")
    _numbers({k: v for k, v in cost.items() if k != "weights"}, "cost")
    try:
        return SchedulingConfig(cost=CostSettings(
            weights=CostWeights(**weights),
            **{k: v for k, v in cost.items() if k != "weights"},
        ))
    except ValidationError as error:
        raise ConfigurationError(f"cost: {error}") from error


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{where}: expected a mapping")
    return value


def _only(raw: Mapping[str, Any], allowed: tuple[str, ...], where: str) -> None:
    for key in raw:
        if key not in allowed:
            raise ConfigurationError(f"{where}: unknown key '{key}'{suggest(str(key), allowed)}")


def _numbers(raw: Mapping[str, Any], where: str) -> None:
    for key, value in raw.items():
        if not is_number(value):
            raise ConfigurationError(f"{where}.{key}: expected a number, got {value!r}")
