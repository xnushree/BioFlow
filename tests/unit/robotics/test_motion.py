"""Tests for cell reservations, deadlock detection, and robot motion controllers."""

import pytest

from bioflow.core.events import Event
from bioflow.core.exceptions import ConfigurationError, SafetyViolationError
from bioflow.core.simulation import SimulationEngine
from bioflow.robotics.deadlock import find_wait_cycle
from bioflow.robotics.map import Cell, EquipmentPlacement, LabMap, Rect
from bioflow.robotics.motion import GridMotion, MotionEvent, TimedMotion
from bioflow.robotics.reservations import CellReservations
from bioflow.robotics.travel import ConstantTravelTime

SPEED = 0.5  # m/min with 0.5 m cells -> each normal step takes exactly 1 minute


# ------------------------------------------------------------ reservations
def test_reservations_are_exclusive() -> None:
    table = CellReservations()

    assert table.try_acquire((0, 0), "R1")
    assert table.try_acquire((0, 0), "R1")  # re-acquiring your own cell is fine
    assert not table.try_acquire((0, 0), "R2")
    assert table.held_by_others("R2") == {(0, 0)}

    table.release((0, 0), "R1")
    assert table.holder((0, 0)) is None


def test_releasing_someone_elses_cell_is_a_safety_violation() -> None:
    table = CellReservations()
    table.try_acquire((0, 0), "R1")

    with pytest.raises(SafetyViolationError):
        table.release((0, 0), "R2")


# ---------------------------------------------------------- deadlock graph
def test_wait_chain_without_cycle() -> None:
    holders = {(1, 0): "R2", (2, 0): "R3"}
    waiting = {"R1": (1, 0), "R2": (2, 0)}  # R3 is not waiting: it will move eventually

    assert find_wait_cycle("R1", waiting, holders.get) is None


def test_two_robot_cycle() -> None:
    holders = {(0, 0): "R1", (1, 0): "R2"}
    waiting = {"R1": (1, 0), "R2": (0, 0)}

    assert find_wait_cycle("R1", waiting, holders.get) == ["R1", "R2"]


def test_cycle_found_behind_a_chain() -> None:
    holders = {(0, 0): "R1", (1, 0): "R2", (2, 0): "R3"}
    waiting = {"R0": (0, 0), "R1": (1, 0), "R2": (2, 0), "R3": (1, 0)}  # R0 queues behind the R2/R3 knot

    assert find_wait_cycle("R0", waiting, holders.get) == ["R2", "R3"]


# ------------------------------------------------------------------ timed
def test_timed_motion_arrives_after_travel_time(engine: SimulationEngine) -> None:
    arrivals: list[float] = []
    TimedMotion(engine, ConstantTravelTime(4.0)).travel("R1", "A", "B", lambda: arrivals.append(engine.now))

    engine.run()

    assert arrivals == [4.0]


# ------------------------------------------------------------------- grid
def place(eid: str, x: int, y: int, access: Cell) -> EquipmentPlacement:
    return EquipmentPlacement(eid, Rect(x, y, 1, 1), access)


class PositionTracker:
    """Rebuilds robot positions from ROBOT_MOVED events and checks nobody ever shares a cell."""

    def __init__(self, engine: SimulationEngine, motion: GridMotion) -> None:
        self.positions = motion.positions()
        self.max_sharing = 1
        engine.bus.subscribe(MotionEvent.ROBOT_MOVED, self.on_move)

    def on_move(self, event: Event) -> None:
        self.positions[event.source] = event.payload["to_cell"]
        cells = list(self.positions.values())
        self.max_sharing = max(self.max_sharing, max(cells.count(c) for c in cells))


def make_grid(engine: SimulationEngine, lab: LabMap, robots: int) -> GridMotion:
    motion = GridMotion(engine, lab, SPEED, lab.parking)
    for n in range(1, robots + 1):
        motion.register(f"R{n}")
    return motion


def events_of(log: list[Event], kind: str) -> list[Event]:
    return [e for e in log if e.event_type == kind]


def test_single_robot_moves_cell_by_cell(engine: SimulationEngine, event_log: list[Event]) -> None:
    lab = LabMap(8, 3, 0.5, placements=[place("DEST_01", 7, 0, (6, 0))], parking=[(0, 0)])
    motion = make_grid(engine, lab, robots=1)
    arrivals: list[float] = []

    motion.travel("R1", "HOME", "DEST_01", lambda: arrivals.append(engine.now))
    engine.run()

    assert arrivals == [6.0]  # 6 steps x 1 minute
    assert motion.position("R1") == (6, 0)
    assert len(events_of(event_log, MotionEvent.ROBOT_MOVED)) == 6


def test_crossing_robots_never_share_a_cell(engine: SimulationEngine) -> None:
    lab = LabMap(7, 7, 0.5, parking=[(0, 3), (3, 0)], placements=[
        place("EAST_01", 6, 3, (5, 3)), place("SOUTH_01", 3, 6, (3, 5)),
    ])
    motion = make_grid(engine, lab, robots=2)
    tracker = PositionTracker(engine, motion)
    arrived: list[str] = []

    motion.travel("R1", "", "EAST_01", lambda: arrived.append("R1"))  # along row 3
    motion.travel("R2", "", "SOUTH_01", lambda: arrived.append("R2"))  # down column 3: paths cross at (3, 3)
    engine.run()

    assert sorted(arrived) == ["R1", "R2"]
    assert tracker.max_sharing == 1


def corridor_with_pocket(length: int, pocket_x: int, parking: list[Cell]) -> LabMap:
    """One-lane corridor on row 0 with equipment at both ends and a single side pocket on row 1."""
    walls = [Rect(x, 1, 1, 1) for x in range(length) if x != pocket_x]
    return LabMap(length, 2, 0.5, blocked=walls, parking=parking, placements=[
        place("WEST_01", 0, 0, (1, 0)), place("EAST_01", length - 1, 0, (length - 2, 0)),
    ])


def test_head_on_deadlock_is_detected_and_resolved_via_side_pocket(
    engine: SimulationEngine, event_log: list[Event]
) -> None:
    motion = make_grid(engine, corridor_with_pocket(9, pocket_x=4, parking=[(2, 0), (6, 0)]), robots=2)
    tracker = PositionTracker(engine, motion)
    arrived: list[str] = []

    motion.travel("R1", "", "EAST_01", lambda: arrived.append("R1"))
    motion.travel("R2", "", "WEST_01", lambda: arrived.append("R2"))
    engine.run()

    assert sorted(arrived) == ["R1", "R2"]
    assert tracker.max_sharing == 1
    assert len(events_of(event_log, MotionEvent.DEADLOCK_DETECTED)) == 1
    resolution = events_of(event_log, MotionEvent.DEADLOCK_RESOLVED)[0].payload
    assert (resolution["yielding_robot"], resolution["strategy"]) == ("R1", "stepped aside")


def test_hopeless_deadlock_fails_safely_instead_of_looping(engine: SimulationEngine) -> None:
    """A dead-end corridor with no pocket: robots can only back off and meet again (livelock)."""
    lab = LabMap(6, 1, 0.5, parking=[(2, 0), (3, 0)], placements=[
        place("WEST_01", 0, 0, (1, 0)), place("EAST_01", 5, 0, (4, 0)),
    ])
    motion = make_grid(engine, lab, robots=2)
    motion.travel("R1", "", "EAST_01", lambda: None)
    motion.travel("R2", "", "WEST_01", lambda: None)

    with pytest.raises(SafetyViolationError, match="unresolvable deadlock"):
        engine.run()


def test_idle_robot_blocking_the_only_route_is_nudged_aside(engine: SimulationEngine) -> None:
    # R2 idles at (2, 0), the only way through; the pocket at (2, 1) lets it step aside.
    lab = LabMap(5, 2, 0.5, parking=[(0, 0), (2, 0)], blocked=[Rect(x, 1, 1, 1) for x in (0, 1, 3, 4)],
                 placements=[place("DEST_01", 4, 0, (3, 0))])
    motion = make_grid(engine, lab, robots=2)
    arrived: list[float] = []

    motion.travel("R1", "", "DEST_01", lambda: arrived.append(engine.now))
    engine.run()

    assert arrived and motion.position("R1") == (3, 0)
    assert motion.position("R2") == (2, 1)


def test_moving_robot_detours_around_idle_robot(engine: SimulationEngine, event_log: list[Event]) -> None:
    lab = LabMap(5, 3, 0.5, parking=[(0, 1), (2, 1)], placements=[place("DEST_01", 4, 1, (3, 1))])
    motion = make_grid(engine, lab, robots=2)  # R2 idles in the middle of the direct route

    motion.travel("R1", "", "DEST_01", lambda: None)
    engine.run()

    assert motion.position("R1") == (3, 1)
    assert motion.position("R2") == (2, 1)  # not disturbed: going around was possible
    assert events_of(event_log, MotionEvent.ROBOT_REROUTED)


def test_idle_robot_returns_to_parking(engine: SimulationEngine) -> None:
    lab = LabMap(6, 2, 0.5, parking=[(0, 0)], placements=[place("DEST_01", 5, 0, (4, 0))])
    motion = make_grid(engine, lab, robots=1)
    motion.travel("R1", "", "DEST_01", lambda: motion.robot_idle("R1"))

    engine.run()

    assert motion.position("R1") == (0, 0)


def test_new_job_in_same_instant_skips_parking(engine: SimulationEngine) -> None:
    lab = LabMap(6, 2, 0.5, parking=[(0, 0)], placements=[
        place("A_01", 5, 0, (4, 0)), place("B_01", 5, 1, (4, 1)),
    ])
    motion = make_grid(engine, lab, robots=1)
    visited: list[Cell] = []

    def at_a() -> None:
        motion.robot_idle("R1")
        motion.travel("R1", "A_01", "B_01", lambda: visited.append(motion.position("R1")))

    motion.travel("R1", "", "A_01", at_a)
    engine.run()

    assert visited == [(4, 1)]  # went straight to B, never headed home


def test_goal_can_change_mid_step(engine: SimulationEngine) -> None:
    lab = LabMap(8, 2, 0.5, parking=[(0, 0)], placements=[
        place("FAR_01", 7, 0, (6, 0)), place("NEAR_01", 3, 1, (4, 1)),
    ])
    motion = make_grid(engine, lab, robots=1)
    arrivals: list[Cell] = []
    motion.travel("R1", "", "FAR_01", lambda: arrivals.append((-1, -1)))

    engine.run(until=0.5)  # halfway through the first step
    motion.travel("R1", "", "NEAR_01", lambda: arrivals.append(motion.position("R1")))
    engine.run()

    assert arrivals == [(4, 1)]


def test_more_robots_than_parking_cells_is_a_configuration_error(engine: SimulationEngine) -> None:
    motion = GridMotion(engine, LabMap(3, 1, 0.5, placements=[], parking=[(0, 0)]), SPEED, [(0, 0)])
    motion.register("R1")

    with pytest.raises(ConfigurationError, match="no free parking cell for R2"):
        motion.register("R2")


# ---------------------------------------------------------- map: parking
@pytest.mark.parametrize(
    ("parking", "message"),
    [
        ([(1, 0), (1, 0)], "must be distinct"),
        ([(0, 0)], "parking cell \\(0, 0\\) is blocked"),
        ([(1, 0)], "is an access point"),
        ([(4, 0)], "cannot be reached"),
    ],
)
def test_parking_validation(parking: list[Cell], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        LabMap(5, 1, 0.5, parking=parking, blocked=[Rect(3, 0, 1, 1)], placements=[place("A_01", 0, 0, (1, 0))])
