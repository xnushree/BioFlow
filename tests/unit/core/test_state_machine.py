"""Tests for TransitionTable and TransitionGuard."""

from dataclasses import dataclass
from enum import StrEnum

import pytest

from bioflow.core.exceptions import InvalidTransitionError
from bioflow.core.state_machine import TransitionGuard, TransitionTable


class Light(StrEnum):
    OFF = "OFF"
    ON = "ON"
    BROKEN = "BROKEN"
    SCRAPPED = "SCRAPPED"


def light_table() -> TransitionTable[Light]:
    return TransitionTable.build(
        Light,
        {Light.OFF: {Light.ON}, Light.ON: {Light.OFF}, Light.BROKEN: {Light.SCRAPPED}},
        from_any={Light.BROKEN},
        terminal={Light.SCRAPPED},
    )


def test_explicit_edges_are_allowed() -> None:
    table = light_table()

    assert table.can(Light.OFF, Light.ON)
    assert table.can(Light.ON, Light.OFF)
    assert not table.can(Light.OFF, Light.SCRAPPED)


def test_from_any_reaches_every_non_terminal_state_except_itself() -> None:
    table = light_table()

    assert table.can(Light.OFF, Light.BROKEN)
    assert table.can(Light.ON, Light.BROKEN)
    assert not table.can(Light.BROKEN, Light.BROKEN)
    assert not table.can(Light.SCRAPPED, Light.BROKEN)


def test_terminal_states_have_no_exits() -> None:
    table = light_table()

    assert table.is_terminal(Light.SCRAPPED)
    assert table.targets(Light.SCRAPPED) == frozenset()
    assert not table.is_terminal(Light.OFF)


def test_check_raises_structured_error() -> None:
    with pytest.raises(InvalidTransitionError) as error:
        light_table().check("LAMP_1", Light.SCRAPPED, Light.ON)

    assert (error.value.entity_id, error.value.from_state, error.value.to_state) == ("LAMP_1", "SCRAPPED", "ON")


@pytest.mark.parametrize(
    ("edges", "kwargs", "message"),
    [
        ({Light.OFF: {Light.ON}, Light.ON: set()}, {}, "no transitions defined"),
        ({Light.OFF: {Light.OFF}, Light.ON: set(), Light.BROKEN: set()}, {"terminal": {Light.SCRAPPED}},
         "self-transition"),
        ({Light.OFF: set(), Light.ON: set(), Light.BROKEN: set(), Light.SCRAPPED: {Light.OFF}},
         {"terminal": {Light.SCRAPPED}}, "terminal states have outgoing edges"),
        ({Light.OFF: {"DIMMED"}, Light.ON: set(), Light.BROKEN: set()}, {"terminal": {Light.SCRAPPED}},
         "unknown target"),
    ],
)
def test_inconsistent_definitions_fail_at_build_time(edges: dict, kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        TransitionTable.build(Light, edges, **kwargs)


@dataclass(eq=False)
class Lamp(TransitionGuard):
    lamp_id: str
    state: Light = Light.OFF

    _state_attr = "state"
    _id_attr = "lamp_id"
    _transitions = light_table()


def test_guard_allows_legal_assignment() -> None:
    lamp = Lamp("LAMP_1")
    lamp.state = Light.ON

    assert lamp.state is Light.ON


def test_guard_rejects_illegal_assignment_and_keeps_old_state() -> None:
    lamp = Lamp("LAMP_1", Light.SCRAPPED)

    with pytest.raises(InvalidTransitionError, match="LAMP_1: invalid transition SCRAPPED -> ON"):
        lamp.state = Light.ON
    assert lamp.state is Light.SCRAPPED


def test_guard_accepts_any_initial_state_and_same_state_reassignment() -> None:
    lamp = Lamp("LAMP_1", Light.SCRAPPED)  # e.g. restored from a database
    lamp.state = Light.SCRAPPED

    assert lamp.state is Light.SCRAPPED


def test_guard_ignores_other_attributes() -> None:
    lamp = Lamp("LAMP_1")
    lamp.lamp_id = "LAMP_2"

    assert lamp.lamp_id == "LAMP_2"
