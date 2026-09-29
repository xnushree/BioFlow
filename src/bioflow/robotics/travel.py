"""How long a robot takes to travel between two pieces of equipment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bioflow.core.validation import require
from bioflow.robotics.map import LabMap
from bioflow.robotics.path_planner import AStarPlanner, Path


class TravelTimeModel(Protocol):
    def travel_time(self, from_id: str, to_id: str) -> float:
        """Minutes to travel from equipment ``from_id`` to ``to_id`` (0 if they are the same)."""
        ...


@dataclass(frozen=True)
class ConstantTravelTime:
    """Every trip between two different locations takes the same time. A simple baseline."""

    minutes: float

    def __post_init__(self) -> None:
        require(self.minutes >= 0, f"travel time must be >= 0, got {self.minutes}")

    def travel_time(self, from_id: str, to_id: str) -> float:
        return 0.0 if from_id == to_id else self.minutes


class MapTravelTime:
    """Travel time from the A* path between two access points on the laboratory map.

        minutes = path cost (cells, zone-weighted) * cell size (m) / robot speed (m/min)

    Paths between fixed equipment never change, so each ordered pair is planned
    once and cached. (Paths that must avoid other robots are planned separately
    in Phase 14 and are not cached.)
    """

    def __init__(self, lab_map: LabMap, robot_speed_m_per_min: float) -> None:
        require(robot_speed_m_per_min > 0, "robot speed must be positive")
        self.map = lab_map
        self.speed = robot_speed_m_per_min
        self._planner = AStarPlanner(lab_map)
        self._cache: dict[tuple[str, str], Path] = {}

    def path(self, from_id: str, to_id: str) -> Path:
        key = (from_id, to_id)
        if key not in self._cache:
            self._cache[key] = self._planner.plan(self.map.access_point(from_id), self.map.access_point(to_id))
        return self._cache[key]

    def travel_time(self, from_id: str, to_id: str) -> float:
        if from_id == to_id:
            return 0.0
        return self.path(from_id, to_id).cost * self.map.cell_size_m / self.speed

    @property
    def cached_paths(self) -> int:
        return len(self._cache)
