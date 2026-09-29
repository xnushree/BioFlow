"""Simulated time.

Simulation time is a float in **minutes** since simulation start. It never
comes from the wall clock: the event engine (Phase 4) jumps it directly to the
timestamp of the next event, which is what lets a 48-hour experiment run in
milliseconds and makes every run reproducible.

Two views of the clock exist on purpose:
    * ``SimulationClock`` can be advanced. Only the event engine holds one.
    * ``Clock`` is a read-only protocol. Every other component (equipment,
      scheduler, fault detector) is given a ``Clock`` and can only read ``now``.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from bioflow.core.exceptions import SimulationError

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400


@runtime_checkable
class Clock(Protocol):
    """Read-only access to the current simulation time."""

    @property
    def now(self) -> float: ...


class SimulationClock:
    """A monotonic simulation clock that is advanced explicitly, never by real time."""

    def __init__(self, start: float = 0.0) -> None:
        self._check_valid_time(start)
        if start < 0:
            raise SimulationError(f"Clock cannot start at negative time {start}")
        self._start = start
        self._now = start

    @property
    def now(self) -> float:
        return self._now

    @property
    def elapsed(self) -> float:
        """Minutes since the clock started."""
        return self._now - self._start

    def advance_to(self, time: float) -> None:
        """Move the clock forward to ``time``.

        Advancing to the current time is allowed, because several events can
        share one timestamp.

        Raises:
            SimulationError: If ``time`` is earlier than ``now`` (causality
                violation) or is NaN/infinite.
        """
        self._check_valid_time(time)
        if time < self._now:
            raise SimulationError(
                f"Cannot move clock backwards from {self._now} to {time}"
            )
        self._now = time

    @staticmethod
    def _check_valid_time(time: float) -> None:
        if not math.isfinite(time):
            raise SimulationError(f"Simulation time must be finite, got {time}")

    def __repr__(self) -> str:
        return f"SimulationClock(now={self._now}, display={format_sim_time(self._now)!r})"


def format_sim_time(minutes: float) -> str:
    """Render simulation minutes as ``'<days>d HH:MM:SS'``, e.g. 1530.5 -> ``'1d 01:30:30'``."""
    if minutes < 0:
        raise ValueError(f"Cannot format negative time {minutes}")
    total_seconds = round(minutes * SECONDS_PER_MINUTE)
    days, remainder = divmod(total_seconds, SECONDS_PER_DAY)
    hours, remainder = divmod(remainder, SECONDS_PER_HOUR)
    mins, secs = divmod(remainder, SECONDS_PER_MINUTE)
    return f"{days}d {hours:02d}:{mins:02d}:{secs:02d}"
