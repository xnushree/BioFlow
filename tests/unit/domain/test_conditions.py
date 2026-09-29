"""Tests for CultureConditions."""

import pytest

from bioflow.core.exceptions import ValidationError
from bioflow.domain import CultureConditions


def test_defaults_describe_a_standard_incubator_environment() -> None:
    conditions = CultureConditions()

    assert (conditions.temperature_c, conditions.co2_pct) == (37.0, 5.0)


@pytest.mark.parametrize(
    ("temperature_c", "co2_pct", "expected"),
    [
        (37.0, 5.0, True),
        (37.5, 5.5, True),  # exactly at tolerance
        (39.0, 5.0, False),  # temperature excursion
        (37.0, 6.0, False),  # CO2 excursion
    ],
)
def test_is_within_tolerance(temperature_c: float, co2_pct: float, expected: bool) -> None:
    assert CultureConditions().is_within(temperature_c, co2_pct) is expected


@pytest.mark.parametrize(
    "kwargs",
    [{"co2_pct": -1.0}, {"co2_pct": 101.0}, {"temperature_tolerance_c": 0.0}],
)
def test_rejects_invalid_values(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        CultureConditions(**kwargs)


def test_is_immutable() -> None:
    with pytest.raises(AttributeError):
        CultureConditions().temperature_c = 40.0  # type: ignore[misc]
