"""Generate randomised but reproducible scenarios from a workload description and a seed.

A WorkloadSpec says what *kind* of load to create (how many plates, how
experiments arrive, which protocols, how tight the deadlines are, which faults
may strike); a seed turns it into one concrete Scenario. Different seeds give
genuinely different workloads, which is what makes a multi-seed benchmark
statistically meaningful. The same seed always gives the same scenario, and
every scheduler is run on the same scenarios, so comparisons are paired.

Deadlines are set relative to each protocol's *nominal duration* (its step
times plus an estimated transport per step): slack 1.0 means "only achievable
with zero waiting"; 1.5 gives 50 % headroom.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bioflow.core.exceptions import ConfigurationError
from bioflow.core.validation import require
from bioflow.domain import EquipmentKind, Protocol
from bioflow.equipment.config import EquipmentConfig, load_equipment_config
from bioflow.faults.fault import VALID_TARGETS, FaultSpec, FaultType, Severity
from bioflow.protocols import load_protocol_library
from bioflow.scenario import ExperimentSpec, Scenario

_KIND_PREFIX = {
    EquipmentKind.ROBOT: ("ROBOT", "robots"), EquipmentKind.INCUBATOR: ("INCUBATOR", "incubators"),
    EquipmentKind.MEDIA_STATION: ("MEDIA", "media_stations"),
    EquipmentKind.IMAGING_STATION: ("IMAGING", "imaging_stations"),
    EquipmentKind.STORAGE: ("STORAGE", "storage"), EquipmentKind.WASTE_STATION: ("WASTE", "waste_stations"),
}
MAX_FAULT_ATTEMPTS = 200


@dataclass(frozen=True)
class FaultPlan:
    count: int
    types: tuple[FaultType, ...]
    window_min: tuple[float, float]  # faults start uniformly inside this window
    duration_min: tuple[float, float]  # and are repaired after a duration in this range

    def __post_init__(self) -> None:
        require(self.count >= 0, "fault count must be >= 0")
        require(bool(self.types), "fault plan needs at least one fault type")
        require(0 <= self.window_min[0] <= self.window_min[1], "fault window must be [start, end]")
        require(0 < self.duration_min[0] <= self.duration_min[1], "fault duration must be [min, max] > 0")


@dataclass(frozen=True)
class WorkloadSpec:
    name: str
    plates: int
    experiment_plates: tuple[int, int]
    arrival_window_min: float
    protocol_mix: Mapping[str, float]
    equipment_config: Path
    protocol_dir: Path
    description: str = ""
    priority_range: tuple[int, int] = (0, 0)
    deadline_slack: tuple[float, float] | None = None
    equipment_overrides: Mapping[str, Any] = field(default_factory=dict)
    laboratory_config: Path | None = None
    scheduling_config: Path | None = None
    fault_config: Path | None = None
    faults: FaultPlan | None = None
    transport_estimate_min: float = 3.0

    def __post_init__(self) -> None:
        require(self.plates >= 1, f"{self.name}: plates must be >= 1")
        low, high = self.experiment_plates
        require(1 <= low <= high, f"{self.name}: experiment_plates must be [min, max] with 1 <= min <= max")
        require(self.arrival_window_min >= 0, f"{self.name}: arrival_window_min must be >= 0")
        require(bool(self.protocol_mix) and all(w > 0 for w in self.protocol_mix.values()),
                f"{self.name}: protocol_mix needs positive weights")
        require(self.priority_range[0] <= self.priority_range[1], f"{self.name}: priority_range must be [min, max]")
        if self.deadline_slack is not None:
            require(1.0 <= self.deadline_slack[0] <= self.deadline_slack[1],
                    f"{self.name}: deadline_slack must be [min, max] with min >= 1")


def nominal_duration(protocol: Protocol, equipment: EquipmentConfig, transport_min: float) -> float:
    """Time a single plate needs with no waiting at all: step durations plus one transport per step."""
    default = {"MEDIA_EXCHANGE": equipment.media_stations.spec.process_time_min,
               "IMAGE": equipment.imaging_stations.spec.process_time_min}
    return sum((step.duration_min or default.get(step.operation, 0.0)) + transport_min for step in protocol.steps)


def equipment_ids(equipment: EquipmentConfig) -> dict[EquipmentKind, list[str]]:
    counts = {"robots": equipment.robots.count, "incubators": equipment.incubators.count,
              "media_stations": equipment.media_stations.count, "imaging_stations": equipment.imaging_stations.count,
              "storage": equipment.storage.count, "waste_stations": equipment.waste_stations.count}
    return {kind: [f"{prefix}_{n:02d}" for n in range(1, counts[section] + 1)]
            for kind, (prefix, section) in _KIND_PREFIX.items()}


def generate_scenario(spec: WorkloadSpec, seed: int, scheduler: str = "fifo") -> Scenario:
    rng = random.Random(seed)
    equipment = load_equipment_config(spec.equipment_config, spec.equipment_overrides)
    protocols = load_protocol_library(spec.protocol_dir)
    unknown = sorted(set(spec.protocol_mix) - set(protocols))
    if unknown:
        raise ConfigurationError(f"{spec.name}: unknown protocols in protocol_mix: {unknown}")

    names, weights = list(spec.protocol_mix), list(spec.protocol_mix.values())
    arrivals: list[tuple[float, str, int]] = []
    remaining = spec.plates
    while remaining > 0:
        plates = min(remaining, rng.randint(*spec.experiment_plates))
        arrivals.append((round(rng.uniform(0, spec.arrival_window_min), 1), rng.choices(names, weights)[0], plates))
        remaining -= plates
    arrivals.sort()

    experiments = []
    for number, (submit, protocol, plates) in enumerate(arrivals, start=1):
        deadline = None
        if spec.deadline_slack is not None:
            nominal = nominal_duration(protocols[protocol], equipment, spec.transport_estimate_min)
            deadline = round(submit + nominal * rng.uniform(*spec.deadline_slack), 1)
        experiments.append(ExperimentSpec(f"{spec.name}{number:03d}", protocol, plates,
                                          rng.randint(*spec.priority_range), submit, deadline))

    return Scenario(
        name=f"{spec.name}-seed{seed}", description=spec.description, seed=seed, scheduler=scheduler,
        equipment_config=spec.equipment_config, protocol_dir=spec.protocol_dir,
        travel_time_min=spec.transport_estimate_min, experiments=tuple(experiments),
        scheduling_config=spec.scheduling_config, laboratory_config=spec.laboratory_config,
        equipment_overrides=spec.equipment_overrides or None,
        faults=_generate_faults(spec.faults, equipment, rng) if spec.faults else (),
        fault_config=spec.fault_config,
    )


def _generate_faults(plan: FaultPlan, equipment: EquipmentConfig, rng: random.Random) -> tuple[FaultSpec, ...]:
    ids = equipment_ids(equipment)
    faults: list[FaultSpec] = []
    for _ in range(MAX_FAULT_ATTEMPTS):
        if len(faults) == plan.count:
            break
        fault_type = rng.choice(plan.types)
        targets = [eid for kind in sorted(VALID_TARGETS[fault_type]) for eid in ids[kind]]
        if not targets:
            continue
        candidate = FaultSpec(fault_type, rng.choice(targets), round(rng.uniform(*plan.window_min), 1),
                              round(rng.uniform(*plan.duration_min), 1), Severity.HIGH)
        if not any(_clash(candidate, other) for other in faults):
            faults.append(candidate)
    if len(faults) < plan.count:
        raise ConfigurationError(f"could only place {len(faults)} of {plan.count} non-overlapping faults")
    return tuple(sorted(faults, key=lambda f: f.start_min))


def _clash(a: FaultSpec, b: FaultSpec) -> bool:
    """Two faults on the same equipment at overlapping times (the injector would reject a same-type overlap,
    and different-type overlaps on one unit make results hard to interpret)."""
    if a.equipment_id != b.equipment_id:
        return False
    a_end, b_end = a.start_min + (a.duration_min or 0), b.start_min + (b.duration_min or 0)
    return a.start_min < b_end and b.start_min < a_end


def scenario_to_yaml(scenario: Scenario) -> str:
    """Serialise a generated scenario so a run can be reproduced or inspected."""
    data: dict[str, Any] = {
        "scenario": scenario.name, "description": scenario.description, "seed": scenario.seed,
        "scheduler": scenario.scheduler, "equipment_config": scenario.equipment_config.as_posix(),
        "protocol_dir": scenario.protocol_dir.as_posix(),
    }
    if scenario.equipment_overrides:
        data["equipment_overrides"] = dict(scenario.equipment_overrides)
    for key in ("scheduling_config", "laboratory_config", "fault_config"):
        value = getattr(scenario, key)
        if value is not None:
            data[key] = value.as_posix()
    if scenario.laboratory_config is None:
        data["travel_time_min"] = scenario.travel_time_min
    data["experiments"] = [
        {"id": e.experiment_id, "protocol": e.protocol, "plates": e.plates, "priority": e.priority,
         "submit_at_min": e.submit_at_min, **({"deadline_min": e.deadline_min} if e.deadline_min else {})}
        for e in scenario.experiments
    ]
    if scenario.faults:
        data["faults"] = [
            {"type": f.fault_type.value, "equipment": f.equipment_id, "at_min": f.start_min,
             "duration_min": f.duration_min, "severity": f.severity.value}
            for f in scenario.faults
        ]
    return yaml.safe_dump(data, sort_keys=False)
