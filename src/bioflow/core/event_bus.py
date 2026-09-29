"""Publish/subscribe event bus with causal (FIFO) delivery and observer isolation.

Publishers announce *that something happened* without knowing who cares.
The scheduler, recovery, telemetry, database logger and dashboard each
subscribe to the event types they need.

Delivery rules:

1. **Causal order.** An event published while another is being delivered
   is queued and delivered only after the current event has reached every
   subscriber. So every subscriber sees events in the order they were
   published. (With naive nested delivery, a subscriber registered late
   could see a follow-up event *before* the event that caused it.)
2. **Synchronous completion.** A publish made from outside any delivery
   returns only after it, and everything it triggered, has been delivered.
   The simulation stays deterministic.
3. **Isolation.** A *critical* subscriber's exception propagates (control
   logic must fail loudly). A non-critical subscriber (an observer such as
   telemetry or a dashboard) is isolated: its exception is logged and
   counted, and delivery to everyone else continues.
"""

from __future__ import annotations

import logging
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass

from bioflow.core.events import ALL_EVENTS, Event, EventHandler

logger = logging.getLogger(__name__)

EventFilter = Callable[[Event], bool]
Unsubscribe = Callable[[], None]


@dataclass(frozen=True)
class _Subscription:
    handler: EventHandler
    where: EventFilter | None
    critical: bool
    name: str


@dataclass(frozen=True)
class BusStats:
    published: dict[str, int]
    handler_errors: dict[str, int]  # subscriber name -> isolated exceptions
    max_queue_depth: int

    @property
    def total_published(self) -> int:
        return sum(self.published.values())


class EventBus:
    """Routes published events to the handlers subscribed to their type."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[_Subscription]] = {}
        self._queue: deque[Event] = deque()
        self._delivering = False
        self._published: Counter[str] = Counter()
        self._errors: Counter[str] = Counter()
        self._max_depth = 0

    def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        *,
        where: EventFilter | None = None,
        critical: bool = True,
        name: str | None = None,
    ) -> Unsubscribe:
        """Call ``handler`` for every published event of ``event_type`` (``ALL_EVENTS`` for all).

        Args:
            where: optional filter; the handler only sees events for which it returns True.
            critical: False for observers whose failures must not disturb control logic.
            name: label used in logs and error statistics (defaults to the handler's name).

        Returns a function that removes this subscription; calling it more than once is harmless.
        """
        subscription = _Subscription(handler, where, critical, name or getattr(handler, "__qualname__", "handler"))
        self._subscribers.setdefault(event_type, []).append(subscription)

        def unsubscribe() -> None:
            subscriptions = self._subscribers.get(event_type, [])
            if subscription in subscriptions:
                subscriptions.remove(subscription)

        return unsubscribe

    def publish(self, event: Event) -> None:
        """Deliver ``event`` (see the module docstring for ordering guarantees)."""
        self._published[event.event_type] += 1
        self._queue.append(event)
        self._max_depth = max(self._max_depth, len(self._queue))
        if self._delivering:
            return  # delivered in order once the current event is finished
        self._delivering = True
        try:
            while self._queue:
                self._deliver(self._queue.popleft())
        finally:
            self._delivering = False
            self._queue.clear()  # a critical failure abandons whatever was still queued

    def _deliver(self, event: Event) -> None:
        # Snapshot, so (un)subscribing during delivery only affects later events.
        subscriptions = [*self._subscribers.get(event.event_type, []), *self._subscribers.get(ALL_EVENTS, [])]
        logger.debug("deliver %s from %s to %d subscriber(s)", event.event_type, event.source, len(subscriptions))
        for subscription in subscriptions:
            if subscription.where is not None and not subscription.where(event):
                continue
            if subscription.critical:
                subscription.handler(event)
                continue
            try:
                subscription.handler(event)
            except Exception:  # an observer must never take the control system down
                self._errors[subscription.name] += 1
                logger.exception("observer %s failed on %s (isolated)", subscription.name, event.event_type)

    def subscriber_count(self, event_type: str) -> int:
        return len(self._subscribers.get(event_type, []))

    @property
    def stats(self) -> BusStats:
        return BusStats(dict(self._published), dict(self._errors), self._max_depth)
