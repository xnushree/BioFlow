"""Discrete-event simulation engine.

The engine keeps a priority queue of *future* events. ``step`` pops the
earliest one, jumps the clock to its timestamp, and calls the handler that was
registered when the event was scheduled. Handlers change state and usually
schedule further events, which is how the simulation moves forward.

The engine also owns the three pieces of shared simulation context that every
component needs, so there is exactly one of each per run:

    * the clock (only the engine advances it),
    * the event bus (for immediate notifications via ``publish``),
    * a seeded random generator (so a run is reproducible from its seed).
"""

from __future__ import annotations

import heapq
import itertools
import logging
import math
import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from bioflow.core.clock import SimulationClock
from bioflow.core.event_bus import EventBus
from bioflow.core.events import Event, EventHandler
from bioflow.core.exceptions import SimulationError

logger = logging.getLogger(__name__)


class SimulationContext(Protocol):
    """What simulated components may do with the engine: read time, schedule
    and cancel their own future events, and publish notifications.

    Components depend on this interface, not on ``SimulationEngine``, so they
    cannot call ``run``/``stop`` and can be tested with a lightweight fake.
    """

    @property
    def now(self) -> float: ...

    def schedule(
        self,
        delay: float,
        event_type: str,
        source: str,
        handler: EventHandler,
        target: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Event: ...

    def cancel(self, event_id: str) -> bool: ...

    def publish(
        self,
        event_type: str,
        source: str,
        target: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Event: ...


@dataclass(order=True)
class _QueueEntry:
    """Heap entry ordered by (timestamp, sequence).

    ``sequence`` is unique and increasing, so events at the same timestamp run
    in the order they were scheduled (FIFO) and the heap never has to compare
    two Events or handlers.
    """

    timestamp: float
    sequence: int
    event: Event = field(compare=False)
    handler: EventHandler = field(compare=False)


class SimulationEngine:
    """Priority-queue discrete-event engine."""

    def __init__(self, start_time: float = 0.0, seed: int = 0, bus: EventBus | None = None) -> None:
        self.clock = SimulationClock(start_time)
        self.bus = bus if bus is not None else EventBus()
        self.rng = random.Random(seed)
        self.seed = seed
        self._queue: list[_QueueEntry] = []
        self._pending_ids: set[str] = set()
        self._cancelled_ids: set[str] = set()
        self._sequence = itertools.count()
        self._events_processed = 0
        self._stop_requested = False

    # ------------------------------------------------------------------ queries
    @property
    def now(self) -> float:
        return self.clock.now

    @property
    def pending_count(self) -> int:
        """Number of scheduled events that have neither run nor been cancelled."""
        return len(self._pending_ids)

    @property
    def events_processed(self) -> int:
        return self._events_processed

    def peek_time(self) -> float | None:
        """Timestamp of the next event that will run, or ``None`` if there is none."""
        self._discard_cancelled_head()
        return self._queue[0].timestamp if self._queue else None

    # --------------------------------------------------------------- scheduling
    def schedule(
        self,
        delay: float,
        event_type: str,
        source: str,
        handler: EventHandler,
        target: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Event:
        """Schedule ``handler`` to run ``delay`` minutes from now. Returns the scheduled Event."""
        if not math.isfinite(delay) or delay < 0:
            raise SimulationError(f"{event_type}: delay must be finite and >= 0, got {delay}")
        return self.schedule_at(self.now + delay, event_type, source, handler, target, payload)

    def schedule_at(
        self,
        timestamp: float,
        event_type: str,
        source: str,
        handler: EventHandler,
        target: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Event:
        """Schedule ``handler`` to run at absolute simulation time ``timestamp``."""
        if timestamp < self.now:
            raise SimulationError(
                f"{event_type}: cannot schedule at {timestamp}, which is before now ({self.now})"
            )
        sequence = next(self._sequence)
        event = Event(self._event_id(sequence), timestamp, event_type, source, target, payload or {})
        heapq.heappush(self._queue, _QueueEntry(timestamp, sequence, event, handler))
        self._pending_ids.add(event.event_id)
        return event

    def cancel(self, event_id: str) -> bool:
        """Prevent a scheduled event from running. Returns False if it is not pending.

        Uses lazy deletion: the entry stays in the heap and is skipped when
        popped, which keeps cancellation O(1) instead of O(n).
        """
        if event_id not in self._pending_ids:
            return False
        self._pending_ids.discard(event_id)
        self._cancelled_ids.add(event_id)
        return True

    def publish(
        self,
        event_type: str,
        source: str,
        target: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Event:
        """Create an Event stamped with the current time and deliver it on the bus now."""
        event = Event(self._event_id(next(self._sequence)), self.now, event_type, source, target, payload or {})
        self.bus.publish(event)
        return event

    # ---------------------------------------------------------------- execution
    def step(self) -> Event | None:
        """Run the next pending event. Returns it, or ``None`` if the queue is empty."""
        self._discard_cancelled_head()
        if not self._queue:
            return None
        entry = heapq.heappop(self._queue)
        self._pending_ids.discard(entry.event.event_id)
        self.clock.advance_to(entry.timestamp)
        logger.debug("run %s %s", entry.event.event_id, entry.event.event_type)
        entry.handler(entry.event)
        self._events_processed += 1
        return entry.event

    def run(self, until: float | None = None, max_events: int | None = None) -> int:
        """Process events until the queue empties, ``until`` is reached, ``max_events``
        have run, or ``stop()`` is called. Returns the number of events processed.

        If ``until`` is given, events at exactly ``until`` still run, and once
        every event up to ``until`` has run the clock is moved to ``until``
        (a stop or ``max_events`` cut-off leaves the clock at the last event).
        """
        if until is not None and until < self.now:
            raise SimulationError(f"Cannot run until {until}, which is before now ({self.now})")
        self._stop_requested = False
        processed = 0
        while not self._stop_requested and (max_events is None or processed < max_events):
            next_time = self.peek_time()
            if next_time is None or (until is not None and next_time > until):
                if until is not None:
                    self.clock.advance_to(until)
                break
            self.step()
            processed += 1
        logger.info("run finished: %d events processed, t=%.3f, %d pending", processed, self.now, self.pending_count)
        return processed

    def stop(self) -> None:
        """Ask ``run`` to return after the event currently being handled."""
        self._stop_requested = True

    # ------------------------------------------------------------------ helpers
    def _discard_cancelled_head(self) -> None:
        while self._queue and self._queue[0].event.event_id in self._cancelled_ids:
            cancelled = heapq.heappop(self._queue)
            self._cancelled_ids.discard(cancelled.event.event_id)

    @staticmethod
    def _event_id(sequence: int) -> str:
        return f"EV{sequence:08d}"
