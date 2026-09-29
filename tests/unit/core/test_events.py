"""Tests for the Event record."""

import math
from enum import StrEnum

import pytest

from bioflow.core.events import ALL_EVENTS, Event
from bioflow.core.exceptions import SimulationError


def test_payload_is_copied_and_read_only() -> None:
    original = {"plate_id": "P017"}
    event = Event("EV1", 1.0, "PLATE_PICKED", "ROBOT_02", payload=original)

    original["plate_id"] = "CHANGED"
    assert event.payload["plate_id"] == "P017"
    with pytest.raises(TypeError):
        event.payload["plate_id"] = "X"  # type: ignore[index]


def test_event_is_immutable() -> None:
    event = Event("EV1", 1.0, "PLATE_PICKED", "ROBOT_02")

    with pytest.raises(AttributeError):
        event.timestamp = 2.0  # type: ignore[misc]


def test_str_enum_event_types_are_accepted() -> None:
    class RobotEvent(StrEnum):
        PLATE_PICKED = "PLATE_PICKED"

    event = Event("EV1", 0.0, RobotEvent.PLATE_PICKED, "ROBOT_01")

    assert event.event_type == "PLATE_PICKED"


@pytest.mark.parametrize(
    ("timestamp", "event_type", "source"),
    [
        (-1.0, "X", "R1"),
        (math.nan, "X", "R1"),
        (0.0, "", "R1"),
        (0.0, ALL_EVENTS, "R1"),  # wildcard is for subscribing, not publishing
        (0.0, "X", ""),
    ],
)
def test_rejects_invalid_events(timestamp: float, event_type: str, source: str) -> None:
    with pytest.raises(SimulationError):
        Event("EV1", timestamp, event_type, source)
