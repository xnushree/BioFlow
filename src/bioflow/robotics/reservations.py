"""Exclusive cell reservations: the rule that makes robot collisions impossible.

A robot must hold the reservation for every cell it occupies, and must
acquire the next cell before stepping into it. A cell has at most one holder.
"""

from __future__ import annotations

from bioflow.core.exceptions import SafetyViolationError
from bioflow.robotics.map import Cell


class CellReservations:
    def __init__(self) -> None:
        self._holder: dict[Cell, str] = {}

    def holder(self, cell: Cell) -> str | None:
        return self._holder.get(cell)

    def try_acquire(self, cell: Cell, robot_id: str) -> bool:
        """Reserve ``cell`` for ``robot_id``. False if another robot holds it."""
        current = self._holder.get(cell)
        if current is not None and current != robot_id:
            return False
        self._holder[cell] = robot_id
        return True

    def release(self, cell: Cell, robot_id: str) -> None:
        current = self._holder.get(cell)
        if current != robot_id:
            raise SafetyViolationError(robot_id, f"releasing cell {cell} held by {current}")
        del self._holder[cell]

    def held_by_others(self, robot_id: str) -> frozenset[Cell]:
        return frozenset(cell for cell, holder in self._holder.items() if holder != robot_id)

    def snapshot(self) -> dict[Cell, str]:
        return dict(self._holder)
