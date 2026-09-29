"""Fixtures shared by all test levels."""

from collections.abc import Callable

import pytest

from bioflow.core.events import ALL_EVENTS, Event
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import CultureConditions, Plate

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
    def factory(plate_id: str = "EXP001-P001", experiment_id: str = "EXP001") -> Plate:
        return Plate(plate_id, experiment_id, "HEK293", 0.0, CultureConditions())

    return factory
