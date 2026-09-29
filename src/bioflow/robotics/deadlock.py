"""Deadlock detection on the robots' wait-for graph.

Each waiting robot waits for exactly one cell, and each cell has at most one
holder, so every robot has at most one outgoing "waits for" edge. Following
those edges from the robot that just started waiting either ends (someone in
the chain can still move) or returns to a robot already visited, which is a
deadlock cycle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from bioflow.robotics.map import Cell


def find_wait_cycle(
    start: str, waiting_for: Mapping[str, Cell], holder_of: Callable[[Cell], str | None]
) -> list[str] | None:
    """The robots in a wait cycle reachable from ``start``, in wait order, or None.

    Args:
        start: the robot that just became blocked.
        waiting_for: robot -> the cell it is waiting to enter (only waiting robots).
        holder_of: cell -> the robot holding it, if any.
    """
    chain: list[str] = []
    position: dict[str, int] = {}
    robot: str | None = start
    while robot is not None and robot in waiting_for:
        if robot in position:
            return chain[position[robot]:]
        position[robot] = len(chain)
        chain.append(robot)
        robot = holder_of(waiting_for[robot])
    return None
