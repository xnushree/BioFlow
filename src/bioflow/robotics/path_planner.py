"""A* shortest-path search on the laboratory grid.

    f(n) = g(n) + w * h(n)
    g: exact cost from the start (sum of step costs of cells entered)
    h: Manhattan distance to the goal * minimum step cost (never overestimates)

With ``heuristic_weight`` = 1 the result is optimal because h is admissible;
with 0 the search degenerates to Dijkstra's algorithm (used in tests as the
reference for optimality).

Ties are broken deterministically (by f, then h, then insertion order), so the
same query always returns the same path.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Collection
from dataclasses import dataclass

from bioflow.core.exceptions import BioFlowError
from bioflow.robotics.map import MIN_STEP_COST, Cell, LabMap


class PathNotFoundError(BioFlowError):
    """No route exists between two cells (walls, or cells temporarily avoided)."""

    def __init__(self, start: Cell, goal: Cell, reason: str = "no route") -> None:
        self.start = start
        self.goal = goal
        super().__init__(f"No path from {start} to {goal}: {reason}")


@dataclass(frozen=True)
class Path:
    cells: tuple[Cell, ...]  # start ... goal, inclusive
    cost: float  # sum of step costs, in cell units

    @property
    def steps(self) -> int:
        return len(self.cells) - 1


@dataclass(frozen=True)
class SearchStats:
    expanded: int  # cells taken off the open list and expanded


class AStarPlanner:
    def __init__(self, lab_map: LabMap, heuristic_weight: float = 1.0) -> None:
        self._map = lab_map
        self._weight = heuristic_weight
        self.last_stats = SearchStats(expanded=0)

    def plan(self, start: Cell, goal: Cell, avoid: Collection[Cell] = frozenset()) -> Path:
        """Cheapest path from ``start`` to ``goal`` that does not enter any cell in ``avoid``.

        ``avoid`` holds temporarily blocked cells, e.g. cells reserved by other robots (Phase 14).

        Raises:
            PathNotFoundError: if either end is impassable or no route exists.
        """
        for cell, name in ((start, "start"), (goal, "goal")):
            if not self._map.is_passable(cell):
                raise PathNotFoundError(start, goal, f"{name} {cell} is not a free cell")
        if goal in avoid:
            raise PathNotFoundError(start, goal, "goal is currently blocked")
        if start == goal:
            self.last_stats = SearchStats(expanded=0)
            return Path((start,), 0.0)

        order = itertools.count()
        open_heap: list[tuple[float, float, int, Cell]] = [(self._h(start, goal), self._h(start, goal), 0, start)]
        best_cost: dict[Cell, float] = {start: 0.0}
        came_from: dict[Cell, Cell] = {}
        closed: set[Cell] = set()

        while open_heap:
            _, _, _, cell = heapq.heappop(open_heap)
            if cell in closed:
                continue  # stale heap entry: a cheaper route to this cell was already expanded
            if cell == goal:
                self.last_stats = SearchStats(expanded=len(closed))
                return Path(self._reconstruct(came_from, goal), best_cost[goal])
            closed.add(cell)
            for neighbour in self._map.neighbours(cell):
                if neighbour in closed or neighbour in avoid:
                    continue
                cost = best_cost[cell] + self._map.step_cost(neighbour)
                if cost < best_cost.get(neighbour, float("inf")):
                    best_cost[neighbour] = cost
                    came_from[neighbour] = cell
                    h = self._h(neighbour, goal)
                    heapq.heappush(open_heap, (cost + h, h, next(order), neighbour))

        self.last_stats = SearchStats(expanded=len(closed))
        raise PathNotFoundError(start, goal)

    def _h(self, cell: Cell, goal: Cell) -> float:
        return self._weight * MIN_STEP_COST * (abs(cell[0] - goal[0]) + abs(cell[1] - goal[1]))

    @staticmethod
    def _reconstruct(came_from: dict[Cell, Cell], goal: Cell) -> tuple[Cell, ...]:
        cells = [goal]
        while cells[-1] in came_from:
            cells.append(came_from[cells[-1]])
        return tuple(reversed(cells))
