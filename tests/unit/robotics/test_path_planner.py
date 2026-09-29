"""Tests for A* path planning and map-based travel times."""

import random

import pytest

from bioflow.core.exceptions import UnknownEntityError
from bioflow.robotics.map import EquipmentPlacement, LabMap, Rect, Zone
from bioflow.robotics.path_planner import AStarPlanner, PathNotFoundError
from bioflow.robotics.travel import MapTravelTime


def open_room(width: int = 10, height: int = 6, blocked=(), zones=()) -> LabMap:
    return LabMap(width, height, 0.5, placements=[], blocked=blocked, zones=zones)


def assert_valid_path(lab: LabMap, cells) -> None:
    for a, b in zip(cells, cells[1:], strict=False):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1, "steps must be 4-connected"
        assert lab.is_passable(b)


def test_straight_line_in_open_room() -> None:
    path = AStarPlanner(open_room()).plan((0, 0), (4, 0))

    assert path.cells == ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0))
    assert (path.steps, path.cost) == (4, 4.0)


def test_start_equals_goal() -> None:
    path = AStarPlanner(open_room()).plan((2, 2), (2, 2))

    assert path.cells == ((2, 2),) and path.cost == 0.0


def test_routes_around_a_wall() -> None:
    lab = open_room(blocked=[Rect(3, 0, 1, 5)])  # wall with a gap at the bottom row

    path = AStarPlanner(lab).plan((0, 0), (6, 0))

    assert (3, 5) in path.cells
    assert_valid_path(lab, path.cells)
    assert path.cost == 16  # down 5 to the gap, right 6, up 5


def test_prefers_detour_when_zone_is_expensive() -> None:
    zone = Zone("slow", Rect(2, 0, 3, 1), 10.0)  # expensive strip on the direct row
    path = AStarPlanner(open_room(zones=[zone])).plan((0, 0), (6, 0))

    assert all(cell[1] == 1 for cell in path.cells[2:5])  # stepped down a row to avoid it
    assert path.cost == 8.0


def test_crosses_zone_when_detour_costs_more() -> None:
    zone = Zone("mild", Rect(0, 0, 10, 6), 1.2)  # the whole room is mildly slow: no way around
    path = AStarPlanner(open_room(zones=[zone])).plan((0, 0), (3, 0))

    assert path.cost == pytest.approx(3 * 1.2)


def test_avoid_cells_forces_another_route() -> None:
    planner = AStarPlanner(open_room())

    path = planner.plan((0, 0), (4, 0), avoid={(2, 0)})

    assert (2, 0) not in path.cells
    assert path.cost == 6.0


@pytest.mark.parametrize(
    ("start", "goal", "avoid", "reason"),
    [
        ((3, 2), (0, 0), frozenset(), "start \\(3, 2\\) is not a free cell"),
        ((0, 0), (99, 0), frozenset(), "goal \\(99, 0\\) is not a free cell"),
        ((0, 0), (4, 0), {(4, 0)}, "goal is currently blocked"),
    ],
)
def test_impossible_requests(start, goal, avoid, reason) -> None:
    lab = open_room(blocked=[Rect(3, 2, 1, 1)])

    with pytest.raises(PathNotFoundError, match=reason):
        AStarPlanner(lab).plan(start, goal, avoid=avoid)


def test_sealed_off_goal() -> None:
    planner = AStarPlanner(open_room())

    with pytest.raises(PathNotFoundError, match="no route"):
        planner.plan((0, 0), (5, 5), avoid={(4, 5), (6, 5), (5, 4)})


def test_same_query_gives_same_path() -> None:
    planner = AStarPlanner(open_room(10, 10))

    assert planner.plan((0, 0), (9, 9)).cells == planner.plan((0, 0), (9, 9)).cells


def random_map(rng: random.Random) -> LabMap:
    blocked = [Rect(rng.randrange(1, 18), rng.randrange(0, 12), 1, rng.randrange(1, 5)) for _ in range(12)]
    zones = [Zone(f"z{i}", Rect(rng.randrange(0, 16), rng.randrange(0, 10), 4, 3), rng.uniform(1.0, 4.0))
             for i in range(3)]
    blocked = [b for b in blocked if b.y + b.height <= 14]
    return LabMap(20, 14, 0.5, placements=[], blocked=blocked, zones=zones)


@pytest.mark.parametrize("seed", range(30))
def test_astar_is_optimal_compared_with_dijkstra(seed: int) -> None:
    rng = random.Random(seed)
    lab = random_map(rng)
    free = [(x, y) for x in range(20) for y in range(14) if lab.is_passable((x, y))]
    start, goal = rng.choice(free), rng.choice(free)
    astar, dijkstra = AStarPlanner(lab), AStarPlanner(lab, heuristic_weight=0.0)

    try:
        expected = dijkstra.plan(start, goal)
    except PathNotFoundError:
        with pytest.raises(PathNotFoundError):
            astar.plan(start, goal)
        return
    path = astar.plan(start, goal)

    assert path.cost == pytest.approx(expected.cost)
    assert path.cells[0] == start and path.cells[-1] == goal
    assert_valid_path(lab, path.cells)
    assert astar.last_stats.expanded <= dijkstra.last_stats.expanded


def test_heuristic_reduces_search_effort_in_open_space() -> None:
    lab = open_room(30, 20)
    astar, dijkstra = AStarPlanner(lab), AStarPlanner(lab, heuristic_weight=0.0)

    astar.plan((0, 0), (29, 19))
    dijkstra.plan((0, 0), (29, 19))

    assert astar.last_stats.expanded < dijkstra.last_stats.expanded / 2


# ------------------------------------------------------------- travel model
def two_station_map() -> LabMap:
    return LabMap(10, 3, 0.5, placements=[
        EquipmentPlacement("A_01", Rect(0, 0, 1, 1), (1, 0)),
        EquipmentPlacement("B_01", Rect(9, 0, 1, 1), (8, 0)),
    ])


def test_travel_time_converts_path_cost_to_minutes() -> None:
    travel = MapTravelTime(two_station_map(), robot_speed_m_per_min=12.0)

    # 7 cells x 0.5 m / 12 m/min
    assert travel.travel_time("A_01", "B_01") == pytest.approx(7 * 0.5 / 12)
    assert travel.travel_time("A_01", "A_01") == 0.0


def test_paths_are_planned_once_and_cached() -> None:
    travel = MapTravelTime(two_station_map(), 12.0)

    first = travel.path("A_01", "B_01")
    assert travel.path("A_01", "B_01") is first
    assert travel.cached_paths == 1


def test_unknown_equipment_in_travel_model() -> None:
    with pytest.raises(UnknownEntityError):
        MapTravelTime(two_station_map(), 12.0).travel_time("A_01", "NOPE_01")
