"""Monitoring and detection settings, loaded from configs/faults.yaml."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from bioflow.core.exceptions import ConfigurationError, ValidationError
from bioflow.core.validation import is_number, require, suggest
from bioflow.faults.monitoring import MonitoringSettings


@dataclass(frozen=True)
class DetectionSettings:
    heartbeat_timeout_min: float = 3.0  # silence longer than this is a symptom
    transport_timeout_factor: float = 3.0  # overdue if > factor x expected + margin
    transport_timeout_margin_min: float = 10.0
    processing_timeout_factor: float = 1.5
    processing_timeout_margin_min: float = 5.0
    excursion_confirmations: int = 2  # consecutive out-of-tolerance readings before diagnosing
    normal_confirmations: int = 2  # consecutive in-tolerance readings before clearing
    stuck_sensor_repeats: int = 3  # identical readings (impossible with real sensor noise)
    pick_failure_threshold: int = 3
    slow_step_factor: float = 2.0  # every recent step's drive time is this much slower than nominal
    slow_step_window: int = 5  # number of recent steps considered
    plausible_temperature_c: tuple[float, float] = (0.0, 60.0)
    plausible_co2_pct: tuple[float, float] = (0.0, 25.0)

    def __post_init__(self) -> None:
        require(self.heartbeat_timeout_min > 0, "heartbeat_timeout_min must be positive")
        require(self.transport_timeout_factor >= 1 and self.processing_timeout_factor >= 1,
                "timeout factors must be >= 1")
        require(self.transport_timeout_margin_min >= 0 and self.processing_timeout_margin_min >= 0,
                "timeout margins must be >= 0")
        require(self.slow_step_factor > 1, "slow_step_factor must be > 1")
        for name in ("excursion_confirmations", "normal_confirmations", "pick_failure_threshold",
                     "slow_step_window"):
            require(getattr(self, name) >= 1, f"{name} must be >= 1")
        require(self.stuck_sensor_repeats >= 2, "stuck_sensor_repeats must be >= 2")
        for name in ("plausible_temperature_c", "plausible_co2_pct"):
            low, high = getattr(self, name)
            require(low < high, f"{name} must be [low, high] with low < high")


@dataclass(frozen=True)
class RecoverySettings:
    max_hold_min: float = 240.0  # how long a safe hold may last before a fault is declared unrecoverable

    def __post_init__(self) -> None:
        require(self.max_hold_min > 0, "max_hold_min must be positive")


@dataclass(frozen=True)
class FaultConfig:
    monitoring: MonitoringSettings = field(default_factory=MonitoringSettings)
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    recovery: RecoverySettings = field(default_factory=RecoverySettings)


def load_fault_config(path: Path) -> FaultConfig:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"Fault config not found: {path}") from None
    except yaml.YAMLError as error:
        raise ConfigurationError(f"{path}: invalid YAML: {error}") from error
    try:
        return parse_fault_config(data)
    except ConfigurationError as error:
        raise ConfigurationError(f"{path.name}: {error}") from error


def parse_fault_config(data: Any) -> FaultConfig:
    raw = data or {}
    if not isinstance(raw, Mapping):
        raise ConfigurationError("fault config: expected a mapping")
    _only(raw, ("monitoring", "detection", "recovery"), "fault config")
    return FaultConfig(
        monitoring=_build(MonitoringSettings, raw.get("monitoring"), "monitoring"),
        detection=_build(DetectionSettings, raw.get("detection"), "detection"),
        recovery=_build(RecoverySettings, raw.get("recovery"), "recovery"),
    )


def _build(cls: type, raw: Any, where: str) -> Any:
    raw = raw or {}
    if not isinstance(raw, Mapping):
        raise ConfigurationError(f"{where}: expected a mapping")
    names = tuple(f.name for f in fields(cls))
    _only(raw, names, where)
    values = {}
    for key, value in raw.items():
        if isinstance(value, list):
            if len(value) != 2 or not all(is_number(v) for v in value):
                raise ConfigurationError(f"{where}.{key}: expected [low, high]")
            value = (float(value[0]), float(value[1]))
        elif not is_number(value):
            raise ConfigurationError(f"{where}.{key}: expected a number, got {value!r}")
        values[key] = value
    try:
        return cls(**values)
    except ValidationError as error:
        raise ConfigurationError(f"{where}: {error}") from error


def _only(raw: Mapping[str, Any], allowed: tuple[str, ...], where: str) -> None:
    for key in raw:
        if key not in allowed:
            raise ConfigurationError(f"{where}: unknown key '{key}'{suggest(str(key), allowed)}")
