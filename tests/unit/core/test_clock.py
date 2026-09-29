"""Tests for the simulation clock."""

import math

import pytest

from bioflow.core.clock import Clock, SimulationClock, format_sim_time
from bioflow.core.exceptions import SimulationError


def test_starts_at_zero_by_default() -> None:
    clock = SimulationClock()

    assert clock.now == 0.0
    assert clock.elapsed == 0.0


def test_can_start_at_a_later_time() -> None:
    clock = SimulationClock(start=100.0)
    clock.advance_to(130.0)

    assert clock.now == 130.0
    assert clock.elapsed == 30.0


def test_advances_forward() -> None:
    clock = SimulationClock()
    clock.advance_to(12.5)
    clock.advance_to(720.0)

    assert clock.now == 720.0


def test_advancing_to_the_same_time_is_allowed() -> None:
    # Several events can share one timestamp.
    clock = SimulationClock()
    clock.advance_to(10.0)
    clock.advance_to(10.0)

    assert clock.now == 10.0


def test_moving_backwards_is_rejected_and_time_is_unchanged() -> None:
    clock = SimulationClock()
    clock.advance_to(50.0)

    with pytest.raises(SimulationError, match="backwards from 50.0 to 49.0"):
        clock.advance_to(49.0)
    assert clock.now == 50.0


@pytest.mark.parametrize("bad_time", [math.nan, math.inf, -math.inf])
def test_non_finite_times_are_rejected(bad_time: float) -> None:
    with pytest.raises(SimulationError, match="finite"):
        SimulationClock().advance_to(bad_time)


def test_negative_start_is_rejected() -> None:
    with pytest.raises(SimulationError, match="negative"):
        SimulationClock(start=-1.0)


def test_simulation_clock_satisfies_read_only_clock_protocol() -> None:
    assert isinstance(SimulationClock(), Clock)


def test_components_can_depend_on_clock_protocol() -> None:
    """A component that only needs to read time accepts any Clock."""

    class FixedClock:
        @property
        def now(self) -> float:
            return 42.0

    def read_time(clock: Clock) -> float:
        return clock.now

    assert read_time(FixedClock()) == 42.0
    assert read_time(SimulationClock(start=7.0)) == 7.0


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (0.0, "0d 00:00:00"),
        (0.5, "0d 00:00:30"),
        (90.0, "0d 01:30:00"),
        (1440.0, "1d 00:00:00"),
        (1530.5, "1d 01:30:30"),
    ],
)
def test_format_sim_time(minutes: float, expected: str) -> None:
    assert format_sim_time(minutes) == expected


def test_format_rejects_negative_time() -> None:
    with pytest.raises(ValueError):
        format_sim_time(-1.0)


def test_repr_shows_human_readable_time() -> None:
    clock = SimulationClock()
    clock.advance_to(90.0)

    assert repr(clock) == "SimulationClock(now=90.0, display='0d 01:30:00')"
