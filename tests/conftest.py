"""Fixtures shared by all test levels."""

from collections.abc import Callable
from typing import Any

import pytest

from bioflow.core.events import ALL_EVENTS, Event
from bioflow.core.simulation import SimulationEngine
from bioflow.core.state_machine import TransitionTable
from bioflow.domain import CultureConditions, Plate, PlateState
from bioflow.equipment.config import parse_equipment_config
from bioflow.laboratory import Laboratory
from bioflow.robotics.travel import ConstantTravelTime
from bioflow.scheduling.registry import create_scheduler

PlateFactory = Callable[..., Plate]


@pytest.fixture
def engine() -> SimulationEngine:
    return SimulationEngine(seed=0)


@pytest.fixture
def event_log(engine: SimulationEngine) -> list[Event]:
    """Every notification published on the engine's bus, in order."""
    log: list[Event] = []
    engine.bus.subscribe(ALL_EVENTS, log.append)
    return log


@pytest.fixture
def make_plate() -> PlateFactory:
    """Plates start IN_TRANSIT by default: as if a robot is delivering them, which is
    the only legal way (besides a new plate entering storage) to arrive at equipment."""

    def factory(
        plate_id: str = "EXP001-P001",
        experiment_id: str = "EXP001",
        state: PlateState = PlateState.IN_TRANSIT,
    ) -> Plate:
        return Plate(plate_id, experiment_id, "HEK293", 0.0, CultureConditions(), state=state)

    return factory


@pytest.fixture
def assert_exact_transitions() -> Callable[[TransitionTable[Any], set[tuple[str, str]]], None]:
    """Check a table allows exactly ``expected`` (from, to) pairs and rejects every other pair.

    Exhaustive on purpose: adding or removing any edge must be a deliberate,
    test-visible decision.
    """

    def check(table: TransitionTable[Any], expected: set[tuple[str, str]]) -> None:
        states = list(table.state_type)
        actual = {(a.value, b.value) for a in states for b in states if table.can(a, b)}
        assert actual - expected == set(), "unexpected transitions allowed"
        assert expected - actual == set(), "expected transitions missing"

    return check


SMALL_LAB: dict[str, Any] = {
    "robots": {"count": 2, "pick_time_min": 0.5, "place_time_min": 0.5},
    "incubators": {"count": 2, "capacity": 10},
    "media_stations": {"count": 1, "process_time_min": 15},
    "imaging_stations": {"count": 1, "process_time_min": 10},
    "storage": {"count": 1, "capacity": 50},
    "waste_stations": {"count": 1, "capacity": 10},
}


@pytest.fixture
def make_lab() -> Callable[..., Laboratory]:
    """Build a fully wired Laboratory; override any SMALL_LAB section, e.g. robots={"count": 1, ...}."""

    def factory(travel_min: float = 2.0, scheduler: str = "fifo", **sections: Any) -> Laboratory:
        config = parse_equipment_config({**SMALL_LAB, **sections})
        return Laboratory(config, create_scheduler(scheduler), ConstantTravelTime(travel_min))

    return factory
