"""The laboratory floor as a 2D grid that robots move on.

Coordinates are (x, y): x is the column (0 .. width-1, left to right), y is the
row (0 .. height-1, top to bottom). Robots move one cell at a time between
4-connected neighbours (no diagonals).

Every step costs at least 1 (zones may only make cells *more* expensive). That
keeps Manhattan distance an admissible A* heuristic in Phase 13: it can never
overestimate the true path cost.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

from bioflow.core.exceptions import ConfigurationError, UnknownEntityError

Cell = tuple[int, int]
MIN_STEP_COST = 1.0
_NEIGHBOUR_OFFSETS = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def cells(self) -> Iterator[Cell]:
        for dy in range(self.height):
            for dx in range(self.width):
                yield (self.x + dx, self.y + dy)

    def contains(self, cell: Cell) -> bool:
        return self.x <= cell[0] < self.x + self.width and self.y <= cell[1] < self.y + self.height

    def is_adjacent(self, cell: Cell) -> bool:
        """True if ``cell`` is outside the rectangle but shares an edge with it."""
        return not self.contains(cell) and any(
            self.contains((cell[0] + dx, cell[1] + dy)) for dx, dy in _NEIGHBOUR_OFFSETS
        )

    def __str__(self) -> str:
        return f"({self.x},{self.y}) {self.width}x{self.height}"


@dataclass(frozen=True)
class EquipmentPlacement:
    equipment_id: str
    footprint: Rect
    access: Cell  # where a robot stands to pick up or place a plate


@dataclass(frozen=True)
class Zone:
    """An area robots may cross, but each cell costs ``cost_multiplier`` (>= 1) instead of 1."""

    name: str
    area: Rect
    cost_multiplier: float


class LabMap:
    """An immutable, validated laboratory layout."""

    def __init__(
        self,
        width: int,
        height: int,
        cell_size_m: float,
        placements: Iterable[EquipmentPlacement],
        blocked: Iterable[Rect] = (),
        zones: Iterable[Zone] = (),
    ) -> None:
        self.width = width
        self.height = height
        self.cell_size_m = cell_size_m
        self._placements = {p.equipment_id: p for p in placements}
        self._blocked_areas = tuple(blocked)
        self._zones = tuple(zones)

        problems = self._check_shapes()
        # Derived lookups are only built from in-bounds geometry.
        self._impassable = frozenset(
            cell
            for area in (*self._blocked_areas, *(p.footprint for p in self._placements.values()))
            for cell in area.cells()
        )
        self._step_cost: dict[Cell, float] = {}
        for zone in self._zones:
            for cell in zone.area.cells():
                self._step_cost[cell] = max(self._step_cost.get(cell, MIN_STEP_COST), zone.cost_multiplier)
        if not problems:
            problems = self._check_access_points() or self._check_connectivity()
        if problems:
            raise ConfigurationError("Invalid laboratory layout:\n" + "\n".join(f"  - {p}" for p in problems))

    # ------------------------------------------------------------------ queries
    @property
    def equipment_ids(self) -> list[str]:
        return sorted(self._placements)

    @property
    def zones(self) -> tuple[Zone, ...]:
        return self._zones

    def placement(self, equipment_id: str) -> EquipmentPlacement:
        try:
            return self._placements[equipment_id]
        except KeyError:
            raise UnknownEntityError(equipment_id, "equipment on the laboratory map") from None

    def access_point(self, equipment_id: str) -> Cell:
        return self.placement(equipment_id).access

    def in_bounds(self, cell: Cell) -> bool:
        return 0 <= cell[0] < self.width and 0 <= cell[1] < self.height

    def is_passable(self, cell: Cell) -> bool:
        return self.in_bounds(cell) and cell not in self._impassable

    def neighbours(self, cell: Cell) -> list[Cell]:
        """Passable 4-connected neighbours, in a fixed order (deterministic path planning)."""
        x, y = cell
        return [n for n in ((x + dx, y + dy) for dx, dy in _NEIGHBOUR_OFFSETS) if self.is_passable(n)]

    def step_cost(self, cell: Cell) -> float:
        """Cost of moving *into* ``cell``: 1, or the highest multiplier of any zone covering it."""
        return self._step_cost.get(cell, MIN_STEP_COST)

    def reachable_from(self, start: Cell) -> set[Cell]:
        """Every passable cell a robot at ``start`` can reach (breadth-first search)."""
        if not self.is_passable(start):
            return set()
        seen = {start}
        queue = deque([start])
        while queue:
            for neighbour in self.neighbours(queue.popleft()):
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        return seen

    def check_covers(self, equipment_ids: Iterable[str]) -> None:
        """Raise if any of ``equipment_ids`` has no position on the map (robots are not placed)."""
        missing = sorted(set(equipment_ids) - set(self._placements))
        if missing:
            raise ConfigurationError(
                f"Laboratory layout has no position for equipment {missing}; add them under 'equipment:'"
            )

    # -------------------------------------------------------------- validation
    def _check_shapes(self) -> list[str]:
        problems = []
        if self.width < 1 or self.height < 1:
            problems.append(f"grid must be at least 1x1, got {self.width}x{self.height}")
        if self.cell_size_m <= 0:
            problems.append(f"cell_size_m must be positive, got {self.cell_size_m}")
        named_areas = [(f"equipment {p.equipment_id}", p.footprint) for p in self._placements.values()]
        named_areas += [(f"blocked area {a}", a) for a in self._blocked_areas]
        for name, area in named_areas + [(f"zone '{z.name}'", z.area) for z in self._zones]:
            if area.width < 1 or area.height < 1:
                problems.append(f"{name}: width and height must be >= 1")
            elif not all(self.in_bounds(c) for c in area.cells()):
                problems.append(f"{name} at {area} lies outside the {self.width}x{self.height} grid")
        for zone in self._zones:
            if zone.cost_multiplier < MIN_STEP_COST:
                problems.append(f"zone '{zone.name}': cost must be >= {MIN_STEP_COST:g}, got {zone.cost_multiplier}")
        for i, (name_a, a) in enumerate(named_areas):
            for name_b, b in named_areas[i + 1:]:
                if set(a.cells()) & set(b.cells()):
                    problems.append(f"{name_a} overlaps {name_b}")
        return problems

    def _check_access_points(self) -> list[str]:
        problems = []
        users: dict[Cell, str] = {}
        for p in self._placements.values():
            if not self.is_passable(p.access):
                problems.append(f"equipment {p.equipment_id}: access point {p.access} is blocked or outside the grid")
            elif not p.footprint.is_adjacent(p.access):
                problems.append(f"equipment {p.equipment_id}: access point {p.access} is not next to its footprint")
            if p.access in users:
                problems.append(f"equipment {p.equipment_id} and {users[p.access]} share access point {p.access}")
            users[p.access] = p.equipment_id
        return problems

    def _check_connectivity(self) -> list[str]:
        if not self._placements:
            return []
        first = next(iter(self._placements.values()))
        reachable = self.reachable_from(first.access)
        cut_off = sorted(p.equipment_id for p in self._placements.values() if p.access not in reachable)
        if cut_off:
            return [f"robots cannot travel between {first.equipment_id} and {cut_off}"]
        return []

    # ---------------------------------------------------------------- display
    def render(self, marks: Mapping[Cell, str] | None = None) -> str:
        """ASCII picture of the layout. ``marks`` overlays characters (e.g. robots, paths)."""
        grid = [["." for _ in range(self.width)] for _ in range(self.height)]
        for zone in self._zones:
            for x, y in zone.area.cells():
                grid[y][x] = "~"
        for area in self._blocked_areas:
            for x, y in area.cells():
                grid[y][x] = "#"
        for p in self._placements.values():
            for x, y in p.footprint.cells():
                grid[y][x] = _symbol(p.equipment_id)
            grid[p.access[1]][p.access[0]] = "+"
        for (x, y), char in (marks or {}).items():
            grid[y][x] = char
        return "\n".join("".join(row) for row in grid)


_SYMBOLS = {"STORAGE": "S", "INCUBATOR": "I", "MEDIA": "M", "IMAGING": "V", "WASTE": "W"}
LEGEND = "S storage  I incubator  M media  V imaging  W waste  + access point  # blocked  ~ slow zone  . floor"


def _symbol(equipment_id: str) -> str:
    return _SYMBOLS.get(equipment_id.split("_")[0], "?")
