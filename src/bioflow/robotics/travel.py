"""How long a robot takes to travel between two pieces of equipment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bioflow.core.validation import require


class TravelTimeModel(Protocol):
    def travel_time(self, from_id: str, to_id: str) -> float:
        """Minutes to travel from equipment ``from_id`` to ``to_id`` (0 if they are the same)."""
        ...


@dataclass(frozen=True)
class ConstantTravelTime:
    """Every trip between two different locations takes the same time.

    A deliberately simple baseline, replaced by map-based travel times in Phase 13.
    """

    minutes: float

    def __post_init__(self) -> None:
        require(self.minutes >= 0, f"travel time must be >= 0, got {self.minutes}")

    def travel_time(self, from_id: str, to_id: str) -> float:
        return 0.0 if from_id == to_id else self.minutes
