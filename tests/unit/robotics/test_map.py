"""Tests for LabMap geometry, queries and validation."""

import re

import pytest

from bioflow.core.exceptions import ConfigurationError, UnknownEntityError
from bioflow.robotics.map import EquipmentPlacement, LabMap, Rect, Zone


def place(equipment_id: str, x: int, y: int, w: int, h: int, access: tuple[int, int]) -> EquipmentPlacement:
    return EquipmentPlacement(equipment_id, Rect(x, y, w, h), access)


def small_map(**overrides) -> LabMap:
    """A 10x6 room: storage on the left, imager on the right, a wall segment in the middle."""
    kwargs = dict(
        width=10, height=6, cell_size_m=0.5,
        placements=[place("STORAGE_01", 0, 0, 2, 2, (2, 0)), place("IMAGING_01", 8, 4, 2, 2, (7, 5))],
        blocked=[Rect(5, 0, 1, 4)],
        zones=[Zone("slow", Rect(3, 4, 2, 2), 3.0)],
    )
    kwargs.update(overrides)
    return LabMap(**kwargs)


def problems_of(**overrides) -> str:
    with pytest.raises(ConfigurationError) as error:
        small_map(**overrides)
    return str(error.value)


# ------------------------------------------------------------------ geometry
def test_rect_cells_contains_and_adjacency() -> None:
    rect = Rect(1, 1, 2, 1)

    assert list(rect.cells()) == [(1, 1), (2, 1)]
    assert rect.contains((2, 1)) and not rect.contains((3, 1))
    assert rect.is_adjacent((0, 1)) and rect.is_adjacent((2, 2))
    assert not rect.is_adjacent((0, 0))  # diagonal only
    assert not rect.is_adjacent((1, 1))  # inside


# ------------------------------------------------------------------- queries
def test_passability_excludes_walls_footprints_and_outside() -> None:
    lab = small_map()

    assert lab.is_passable((2, 0))  # access point
    assert not lab.is_passable((0, 0))  # storage footprint
    assert not lab.is_passable((5, 2))  # wall
    assert not lab.is_passable((-1, 0)) and not lab.is_passable((10, 0))


def test_neighbours_are_passable_and_in_fixed_order() -> None:
    lab = small_map()

    assert lab.neighbours((4, 1)) == [(3, 1), (4, 2), (4, 0)]  # (5,1) is the wall
    assert lab.neighbours((4, 1)) == lab.neighbours((4, 1))


def test_step_cost_is_one_except_in_zones() -> None:
    lab = small_map()

    assert lab.step_cost((0, 5)) == 1.0
    assert lab.step_cost((4, 5)) == 3.0


def test_overlapping_zones_use_the_highest_cost() -> None:
    lab = small_map(zones=[Zone("a", Rect(3, 4, 2, 2), 2.0), Zone("b", Rect(4, 4, 1, 1), 4.0)])

    assert lab.step_cost((3, 4)) == 2.0
    assert lab.step_cost((4, 4)) == 4.0


def test_reachability_goes_around_the_wall() -> None:
    lab = small_map()

    reachable = lab.reachable_from(lab.access_point("STORAGE_01"))

    assert lab.access_point("IMAGING_01") in reachable
    assert (5, 2) not in reachable
    assert lab.reachable_from((5, 2)) == set()  # starting inside a wall


def test_access_point_lookup() -> None:
    lab = small_map()

    assert lab.access_point("IMAGING_01") == (7, 5)
    with pytest.raises(UnknownEntityError, match="laboratory map"):
        lab.access_point("MEDIA_09")


def test_check_covers_reports_missing_equipment() -> None:
    lab = small_map()

    lab.check_covers(["STORAGE_01"])
    with pytest.raises(ConfigurationError, match=r"\['INCUBATOR_01', 'MEDIA_01'\]"):
        lab.check_covers(["STORAGE_01", "MEDIA_01", "INCUBATOR_01"])


def test_render_draws_every_feature_and_marks() -> None:
    rows = small_map().render(marks={(0, 5): "R"}).splitlines()

    assert rows[0] == "SS+..#...."
    assert rows[4] == "...~~...VV"
    assert rows[5] == "R..~~..+VV"


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"placements": [place("STORAGE_01", 9, 0, 2, 1, (8, 0))]}, "STORAGE_01 at .* lies outside the 10x6 grid"),
        ({"blocked": [Rect(1, 1, 2, 2)]}, "equipment STORAGE_01 overlaps blocked area"),
        ({"zones": [Zone("bad", Rect(0, 5, 2, 1), 0.5)]}, "cost must be >= 1"),
        ({"zones": [Zone("big", Rect(0, 0, 20, 1), 2.0)]}, "zone 'big' .* outside"),
        ({"width": 0}, "grid must be at least 1x1"),
        ({"cell_size_m": 0}, "cell_size_m must be positive"),
    ],
)
def test_shape_problems(overrides: dict, message: str) -> None:
    assert re.search(message, problems_of(**overrides))


def test_access_point_must_be_free_and_adjacent() -> None:
    text = problems_of(placements=[
        place("STORAGE_01", 0, 0, 2, 2, (1, 1)),  # inside its own footprint
        place("IMAGING_01", 8, 4, 2, 2, (6, 5)),  # two cells away
    ])

    assert "STORAGE_01: access point (1, 1) is blocked" in text
    assert "IMAGING_01: access point (6, 5) is not next to its footprint" in text


def test_access_points_cannot_be_shared() -> None:
    text = problems_of(placements=[place("A_01", 0, 0, 1, 1, (1, 0)), place("B_01", 2, 0, 1, 1, (1, 0))])

    assert "share access point (1, 0)" in text


def test_unreachable_equipment_is_reported() -> None:
    wall_across_the_room = [Rect(5, 0, 1, 6)]

    assert "robots cannot travel between STORAGE_01 and ['IMAGING_01']" in problems_of(blocked=wall_across_the_room)


def test_obstacles_may_overlap_each_other() -> None:
    lab = small_map(blocked=[Rect(5, 0, 1, 4), Rect(5, 2, 1, 2)])  # an L-shaped wall drawn as two rectangles

    assert not lab.is_passable((5, 3))


def test_all_problems_are_reported_together() -> None:
    text = problems_of(blocked=[Rect(1, 1, 2, 2), Rect(20, 0, 1, 1)])

    assert text.count("\n  - ") == 2
