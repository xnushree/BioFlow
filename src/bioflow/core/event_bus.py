"""Synchronous publish/subscribe event bus.

Publishers announce *that something happened* without knowing who cares.
The scheduler, telemetry, database logger and dashboard each subscribe to the
event types they need, so none of them is wired directly to the equipment.

Delivery is synchronous and in subscription order: ``publish`` returns only
after every subscriber has run. That keeps the simulation deterministic.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable

from bioflow.core.events import ALL_EVENTS, Event, EventHandler

logger = logging.getLogger(__name__)

Unsubscribe = Callable[[], None]


class EventBus:
    """Routes published events to the handlers subscribed to their type."""

    def __init__(self) -> None:
        self._subscribers: defaultdict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> Unsubscribe:
        """Call ``handler`` for every published event of ``event_type``.

        Use ``ALL_EVENTS`` to receive everything. Returns a function that
        removes this subscription; calling it more than once is harmless.
        """
        self._subscribers[event_type].append(handler)

        def unsubscribe() -> None:
            handlers = self._subscribers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    def publish(self, event: Event) -> None:
        """Deliver ``event`` to type-specific subscribers, then to ``ALL_EVENTS`` subscribers.

        Handlers are snapshotted first, so a handler that subscribes or
        unsubscribes during delivery affects only later events.
        """
        handlers = [
            *self._subscribers.get(event.event_type, []),
            *self._subscribers.get(ALL_EVENTS, []),
        ]
        logger.debug("publish %s from %s to %d handler(s)", event.event_type, event.source, len(handlers))
        for handler in handlers:
            handler(event)

    def subscriber_count(self, event_type: str) -> int:
        return len(self._subscribers.get(event_type, []))
