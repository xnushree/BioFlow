"""How robots physically get from one place to another.

``MotionController`` is the interface a Robot uses: "take me to equipment X,
then call me back". Two implementations:

* ``TimedMotion``: a trip is a single timed event (no map; robots never
  interact). Used with a fixed travel time.
* ``GridMotion``: robots move cell by cell along A* paths with cell
  reservations, waiting, deadlock detection and recovery, and parking.

GridMotion rules, in order of precedence:

1. A robot holds the cell it stands on and must acquire the next cell before
   entering it, so two robots never share a cell.
2. A blocked robot waits and is woken when the cell is released.
3. Blocked by an idle (parked) robot: replan around it; if impossible, nudge
   the idle robot to a free neighbouring cell.
4. Every new wait triggers deadlock detection. In a cycle, robots yield in
   priority order (highest ID first), escalating through: replan around the
   others; step aside into a cell off the others' paths; back off to any free
   cell. If no robot can move, or the same robots keep deadlocking without
   progress (livelock), motion stops with a SafetyViolationError.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from bioflow.core.exceptions import ConfigurationError, SafetyViolationError
from bioflow.core.simulation import SimulationContext
from bioflow.robotics.deadlock import find_wait_cycle
from bioflow.robotics.map import Cell, LabMap
from bioflow.robotics.path_planner import AStarPlanner, PathNotFoundError
from bioflow.robotics.reservations import CellReservations
from bioflow.robotics.travel import TravelTimeModel

logger = logging.getLogger(__name__)

ArrivalCallback = Callable[[], None]
SOURCE_ID = "MOTION"
# The same robots deadlocking this many times without any of them reaching its goal is
# treated as a livelock (e.g. two robots backing off in a dead-end corridor forever).
MAX_REPEATED_DEADLOCKS = 20


class MotionController(Protocol):
    def register(self, robot_id: str) -> None:
        """Called once per robot when it is created."""
        ...

    def travel(self, robot_id: str, from_id: str, to_id: str, on_arrival: ArrivalCallback) -> None:
        """Move the robot from equipment ``from_id`` to equipment ``to_id``, then call ``on_arrival``."""
        ...

    def robot_idle(self, robot_id: str) -> None:
        """The robot finished its job and has nothing to do."""
        ...

    def freeze(self, robot_id: str) -> None:
        """Stop the robot where it is (hardware failure). Its current trip is suspended."""
        ...

    def resume(self, robot_id: str) -> None:
        """Continue a suspended trip."""
        ...

    def set_speed_factor(self, robot_id: str, factor: float) -> None:
        """Multiply the robot's travel times by ``factor`` (> 1 = slower, e.g. a degraded drive)."""
        ...

    def cancel_trip(self, robot_id: str) -> None:
        """Forget the robot's current destination; it stays where it is."""
        ...


class MotionEvent(StrEnum):
    ROBOT_MOVED = "ROBOT_MOVED"  # payload: from_cell, to_cell, step_min (drive time), step_cost (map cost)
    ROBOT_WAITING = "ROBOT_WAITING"  # payload: cell, blocked_by
    ROBOT_REROUTED = "ROBOT_REROUTED"  # payload: reason
    DEADLOCK_DETECTED = "DEADLOCK_DETECTED"  # payload: robots
    DEADLOCK_RESOLVED = "DEADLOCK_RESOLVED"  # payload: robots, yielding_robot, strategy


# ------------------------------------------------------------------ timed
class TimedMotion:
    """Trips take ``travel.travel_time(from, to)`` minutes; robots do not interact."""

    def __init__(self, context: SimulationContext, travel: TravelTimeModel) -> None:
        self._context = context
        self._travel = travel
        self._speed_factor: dict[str, float] = {}
        self._trips: dict[str, _Trip] = {}

    def register(self, robot_id: str) -> None:
        self._speed_factor[robot_id] = 1.0

    def travel(self, robot_id: str, from_id: str, to_id: str, on_arrival: ArrivalCallback) -> None:
        minutes = self._travel.travel_time(from_id, to_id) * self._speed_factor.get(robot_id, 1.0)
        self._start_trip(robot_id, minutes, on_arrival)

    def robot_idle(self, robot_id: str) -> None:
        pass

    def freeze(self, robot_id: str) -> None:
        trip = self._trips.get(robot_id)
        if trip is not None and trip.event_id is not None:
            self._context.cancel(trip.event_id)
            trip.remaining = trip.due - self._context.now
            trip.event_id = None

    def resume(self, robot_id: str) -> None:
        trip = self._trips.get(robot_id)
        if trip is not None and trip.remaining is not None:
            self._start_trip(robot_id, trip.remaining, trip.on_arrival)

    def set_speed_factor(self, robot_id: str, factor: float) -> None:
        self._speed_factor[robot_id] = factor

    def cancel_trip(self, robot_id: str) -> None:
        trip = self._trips.pop(robot_id, None)
        if trip is not None and trip.event_id is not None:
            self._context.cancel(trip.event_id)

    def _start_trip(self, robot_id: str, minutes: float, on_arrival: ArrivalCallback) -> None:
        trip = _Trip(on_arrival, due=self._context.now + minutes)
        self._trips[robot_id] = trip

        def arrive(event: object) -> None:
            del self._trips[robot_id]
            on_arrival()

        trip.event_id = self._context.schedule(minutes, "ROBOT_ARRIVED", robot_id, arrive).event_id


@dataclass
class _Trip:
    on_arrival: ArrivalCallback
    due: float
    event_id: str | None = None
    remaining: float | None = None  # set while frozen


# ------------------------------------------------------------------- grid
@dataclass
class _RobotMotion:
    robot_id: str
    cell: Cell
    parking: Cell
    goal: Cell | None = None
    on_arrival: ArrivalCallback | None = None
    path: list[Cell] = field(default_factory=list)  # remaining cells, excluding the current one
    stepping: bool = False  # between acquiring the next cell and arriving in it
    idle: bool = True  # no job: may be nudged out of the way
    waiting_for: Cell | None = None
    frozen: bool = False  # hardware failure: an obstacle until resumed
    speed_factor: float = 1.0
    step_event_id: str | None = None
    step_started_at: float = 0.0


@dataclass(frozen=True)
class MotionStats:
    steps: int
    waits: int
    reroutes: int
    deadlocks: int


class GridMotion:
    def __init__(
        self,
        context: SimulationContext,
        lab_map: LabMap,
        robot_speed_m_per_min: float,
        parking: Iterable[Cell],
    ) -> None:
        self._context = context
        self._map = lab_map
        self._minutes_per_cost = lab_map.cell_size_m / robot_speed_m_per_min
        self._planner = AStarPlanner(lab_map)
        self._reservations = CellReservations()
        self._free_parking = list(parking)
        self._robots: dict[str, _RobotMotion] = {}
        self._waiters: dict[Cell, list[str]] = {}
        self._steps = self._waits = self._reroutes = self._deadlocks = 0
        self._repeat_deadlocks: dict[frozenset[str], int] = {}

    # --------------------------------------------------------------- queries
    def position(self, robot_id: str) -> Cell:
        return self._robots[robot_id].cell

    def positions(self) -> dict[str, Cell]:
        return {rid: m.cell for rid, m in self._robots.items()}

    def planned_path(self, robot_id: str) -> list[Cell]:
        return list(self._robots[robot_id].path)

    @property
    def stats(self) -> MotionStats:
        return MotionStats(self._steps, self._waits, self._reroutes, self._deadlocks)

    # ------------------------------------------------------------- interface
    def register(self, robot_id: str) -> None:
        if not self._free_parking:
            raise ConfigurationError(f"Laboratory layout has no free parking cell for {robot_id}")
        spot = self._free_parking.pop(0)
        if not self._reservations.try_acquire(spot, robot_id):
            raise ConfigurationError(f"Parking cell {spot} for {robot_id} is already occupied")
        self._robots[robot_id] = _RobotMotion(robot_id, cell=spot, parking=spot)

    def travel(self, robot_id: str, from_id: str, to_id: str, on_arrival: ArrivalCallback) -> None:
        motion = self._robots[robot_id]
        motion.idle = False
        self._set_goal(motion, self._map.access_point(to_id), on_arrival)

    def freeze(self, robot_id: str) -> None:
        motion = self._robots[robot_id]
        motion.frozen = True
        if motion.stepping:  # abandon the half-finished step: stay in the current cell
            assert motion.step_event_id is not None
            self._context.cancel(motion.step_event_id)
            motion.stepping = False
            self._reservations.release(motion.path[0], robot_id)
            self._wake(motion.path[0])
        if motion.waiting_for is not None:
            self._stop_waiting(motion)

    def resume(self, robot_id: str) -> None:
        motion = self._robots[robot_id]
        if not motion.frozen:
            return  # resuming twice must never schedule a second, overlapping step
        motion.frozen = False
        self._advance(motion)

    def set_speed_factor(self, robot_id: str, factor: float) -> None:
        self._robots[robot_id].speed_factor = factor

    def cancel_trip(self, robot_id: str) -> None:
        motion = self._robots[robot_id]
        motion.goal, motion.on_arrival = None, None
        motion.path = motion.path[:1] if motion.stepping else []
        if motion.waiting_for is not None:
            self._stop_waiting(motion)

    def robot_idle(self, robot_id: str) -> None:
        motion = self._robots[robot_id]
        motion.idle = True
        # Deferred by a zero-delay event: if the dispatcher gives the robot a new job in
        # the same instant, it goes there directly instead of starting towards parking.
        self._context.schedule(0.0, "PARKING_CHECK", robot_id, lambda event: self._park(robot_id))

    # ------------------------------------------------------------ movement
    def _park(self, robot_id: str) -> None:
        motion = self._robots[robot_id]
        if motion.idle and not motion.frozen and motion.goal is None and motion.cell != motion.parking:
            self._set_goal(motion, motion.parking, None)

    def _set_goal(self, motion: _RobotMotion, goal: Cell, on_arrival: ArrivalCallback | None) -> None:
        motion.goal = goal
        motion.on_arrival = on_arrival
        # Keep the cell a robot is currently stepping into; the rest is replanned for the new goal.
        motion.path = motion.path[:1] if motion.stepping else []
        if motion.waiting_for is not None:
            self._stop_waiting(motion)
        if not motion.stepping:  # otherwise the new goal is picked up when the current step ends
            self._advance(motion)

    def _advance(self, motion: _RobotMotion) -> None:
        """Take the next step towards the goal, arrive, or start waiting."""
        if motion.goal is None or motion.frozen:
            return
        if motion.cell == motion.goal:
            self._forget_deadlocks(motion.robot_id)  # progress: past deadlocks involving it are settled
            callback, motion.goal, motion.on_arrival = motion.on_arrival, None, None
            if callback is not None:
                self._context.schedule(0.0, "ROBOT_ARRIVED", motion.robot_id, lambda event: callback())
            return
        if not motion.path:
            try:
                motion.path = list(self._planner.plan(motion.cell, motion.goal).cells[1:])
            except PathNotFoundError as error:
                raise SafetyViolationError(motion.robot_id, f"no route to goal: {error}") from error

        next_cell = motion.path[0]
        if self._reservations.try_acquire(next_cell, motion.robot_id):
            motion.stepping = True
            motion.step_started_at = self._context.now
            delay = self._map.step_cost(next_cell) * self._minutes_per_cost * motion.speed_factor
            motion.step_event_id = self._context.schedule(
                delay, "ROBOT_STEP", motion.robot_id, lambda event: self._finish_step(motion)
            ).event_id
        else:
            self._wait(motion, next_cell)

    def _finish_step(self, motion: _RobotMotion) -> None:
        previous, motion.cell = motion.cell, motion.path.pop(0)
        motion.stepping = False
        self._reservations.release(previous, motion.robot_id)
        self._steps += 1
        # Position tracking sees when the robot left one cell and entered the next, so the pure
        # drive time of the step is observable (waiting before the step is not included).
        self._context.publish(MotionEvent.ROBOT_MOVED, motion.robot_id, payload={
            "from_cell": previous, "to_cell": motion.cell,
            "step_min": self._context.now - motion.step_started_at, "step_cost": self._map.step_cost(motion.cell),
        })
        self._wake(previous)
        self._advance(motion)

    # -------------------------------------------------------------- waiting
    def _wait(self, motion: _RobotMotion, cell: Cell) -> None:
        blocker_id = self._reservations.holder(cell)
        assert blocker_id is not None
        motion.waiting_for = cell
        self._waiters.setdefault(cell, []).append(motion.robot_id)
        self._waits += 1
        self._context.publish(MotionEvent.ROBOT_WAITING, motion.robot_id,
                              payload={"cell": cell, "blocked_by": blocker_id})

        cycle = find_wait_cycle(
            motion.robot_id,
            {rid: m.waiting_for for rid, m in self._robots.items() if m.waiting_for is not None},
            self._reservations.holder,
        )
        if cycle:
            self._resolve_deadlock(cycle)
            return
        blocker = self._robots[blocker_id]
        if blocker.frozen:  # a broken-down robot is an obstacle: go around it if possible, else wait
            self._reroute(motion, avoid={blocker.cell}, reason=f"{blocker.robot_id} broken down")
        elif blocker.idle and blocker.goal is None and not blocker.stepping:
            self._get_past_idle_robot(motion, blocker)

    def _stop_waiting(self, motion: _RobotMotion) -> None:
        assert motion.waiting_for is not None
        self._waiters[motion.waiting_for].remove(motion.robot_id)
        motion.waiting_for = None

    def _wake(self, cell: Cell) -> None:
        for robot_id in self._waiters.pop(cell, []):
            motion = self._robots[robot_id]
            motion.waiting_for = None
            if not motion.stepping:
                self._advance(motion)

    def _get_past_idle_robot(self, motion: _RobotMotion, blocker: _RobotMotion) -> None:
        if self._reroute(motion, avoid={blocker.cell}, reason=f"idle {blocker.robot_id} in the way"):
            return
        aside = self._free_neighbour(blocker.cell, exclude=set(motion.path))
        if aside is not None:
            self._set_goal(blocker, aside, None)

    # ------------------------------------------------------------- deadlock
    def _resolve_deadlock(self, cycle: list[str]) -> None:
        self._deadlocks += 1
        key = frozenset(cycle)
        self._repeat_deadlocks[key] = self._repeat_deadlocks.get(key, 0) + 1
        if self._repeat_deadlocks[key] > MAX_REPEATED_DEADLOCKS:
            raise SafetyViolationError(
                ",".join(sorted(cycle)), f"unresolvable deadlock: livelock after {MAX_REPEATED_DEADLOCKS} attempts"
            )
        logger.info("deadlock among %s", cycle)
        self._context.publish(MotionEvent.DEADLOCK_DETECTED, SOURCE_ID, payload={"robots": sorted(cycle)})
        yield_order = sorted(cycle, reverse=True)  # highest ID yields first

        # 1. Replan: a route that avoids every cell other robots hold.
        for robot_id in yield_order:
            motion = self._robots[robot_id]
            if self._reroute(motion, avoid=self._reservations.held_by_others(robot_id), reason="deadlock"):
                self._report_resolution(cycle, robot_id, "replanned")
                return
        # 2. Step aside: into a free cell that is not on another cycle robot's path, so it can pass.
        for robot_id in yield_order:
            others_paths = {cell for other in cycle if other != robot_id for cell in self._robots[other].path}
            if self._step_to(robot_id, self._free_neighbour(self._robots[robot_id].cell, exclude=others_paths)):
                self._report_resolution(cycle, robot_id, "stepped aside")
                return
        # 3. Back off: any free neighbouring cell (last resort; may need repeating).
        for robot_id in yield_order:
            if self._step_to(robot_id, self._free_neighbour(self._robots[robot_id].cell, exclude=set())):
                self._report_resolution(cycle, robot_id, "backed off")
                return
        raise SafetyViolationError(",".join(sorted(cycle)), "unresolvable deadlock: no robot can move aside")

    def _step_to(self, robot_id: str, cell: Cell | None) -> bool:
        """Make a waiting robot take one step to ``cell``; afterwards it replans towards its goal."""
        if cell is None:
            return False
        motion = self._robots[robot_id]
        self._stop_waiting(motion)
        motion.path = [cell]
        self._reroutes += 1
        self._advance(motion)
        return True

    def _forget_deadlocks(self, robot_id: str) -> None:
        for key in [k for k in self._repeat_deadlocks if robot_id in k]:
            del self._repeat_deadlocks[key]

    def _report_resolution(self, cycle: list[str], robot_id: str, strategy: str) -> None:
        self._context.publish(MotionEvent.DEADLOCK_RESOLVED, SOURCE_ID,
                              payload={"robots": sorted(cycle), "yielding_robot": robot_id, "strategy": strategy})

    def _reroute(self, motion: _RobotMotion, avoid: Iterable[Cell], reason: str) -> bool:
        if motion.goal is None:
            return False
        try:
            path = self._planner.plan(motion.cell, motion.goal, avoid=frozenset(avoid))
        except PathNotFoundError:
            return False
        if motion.waiting_for is not None:
            self._stop_waiting(motion)
        motion.path = list(path.cells[1:])
        self._reroutes += 1
        self._context.publish(MotionEvent.ROBOT_REROUTED, motion.robot_id, payload={"reason": reason})
        self._advance(motion)
        return True

    def _free_neighbour(self, cell: Cell, exclude: set[Cell]) -> Cell | None:
        for neighbour in self._map.neighbours(cell):
            if neighbour not in exclude and self._reservations.holder(neighbour) is None:
                return neighbour
        return None
