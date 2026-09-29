"""Fixtures shared by all test levels."""

from collections.abc import Callable
from typing import Any

import pytest

from bioflow.core.events import ALL_EVENTS, Event
from bioflow.core.simulation import SimulationEngine
from bioflow.core.state_machine import TransitionTable
from bioflow.domain import CultureConditions, Plate, PlateState

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
