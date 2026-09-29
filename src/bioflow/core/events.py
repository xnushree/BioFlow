"""The Event record shared by the simulation engine and the event bus.

``event_type`` is a plain string on purpose. ``core`` is the lowest layer and
must not know about robots or incubators, so each higher-level module defines
its own event types as a ``StrEnum`` (whose members *are* strings) and ``core``
handles them generically.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from bioflow.core.exceptions import SimulationError

ALL_EVENTS = "*"
"""Subscribe with this event type to receive every published event."""

_EMPTY_PAYLOAD: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class Event:
    """Something that happens (or will happen) at a point in simulation time.

    Attributes:
        event_id: Unique, deterministic ID assigned by the engine (``EV00000001``).
        timestamp: Simulation time in minutes.
        event_type: What happened, e.g. ``"PLATE_PICKED"``.
        source: ID of the component that produced the event, e.g. ``"ROBOT_02"``.
        target: Optional ID of the component it concerns, e.g. ``"MEDIA_01"``.
        payload: Read-only extra data, e.g. ``{"plate_id": "EXP001-P017"}``.
    """

    event_id: str
    timestamp: float
    event_type: str
    source: str
    target: str | None = None
    payload: Mapping[str, Any] = field(default=_EMPTY_PAYLOAD, hash=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.timestamp) or self.timestamp < 0:
            raise SimulationError(f"Invalid event timestamp {self.timestamp}")
        if not self.event_type or self.event_type == ALL_EVENTS:
            raise SimulationError(f"Invalid event type {self.event_type!r}")
        if not self.source:
            raise SimulationError(f"{self.event_type}: event source must not be empty")
        # Copy then freeze, so neither the caller nor any handler can mutate it later.
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


EventHandler = Callable[[Event], None]
