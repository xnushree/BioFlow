"""Tests for Plate."""

import pytest

from bioflow.core.exceptions import SimulationError, ValidationError
from bioflow.domain import ContaminationStatus, CultureConditions, Plate, PlateState


def make_plate(**overrides: object) -> Plate:
    fields: dict[str, object] = {
        "plate_id": "EXP001-P001",
        "experiment_id": "EXP001",
        "cell_type": "HEK293",
        "created_at": 10.0,
        "conditions": CultureConditions(),
    }
    fields.update(overrides)
    return Plate(**fields)  # type: ignore[arg-type]


def test_new_plate_defaults() -> None:
    plate = make_plate()

    assert plate.state is PlateState.CREATED
    assert plate.contamination is ContaminationStatus.CLEAN
    assert plate.location_id is None
    assert not plate.is_finished


def test_culture_age_is_derived_from_creation_time() -> None:
    assert make_plate(created_at=10.0).culture_age_min(now=130.0) == 120.0


def test_culture_age_before_creation_is_an_error() -> None:
    with pytest.raises(SimulationError):
        make_plate(created_at=10.0).culture_age_min(now=5.0)


@pytest.mark.parametrize(
    "overrides", [{"plate_id": ""}, {"experiment_id": " "}, {"created_at": -1.0}]
)
def test_rejects_invalid_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_plate(**overrides)


@pytest.mark.parametrize("state", [PlateState.ARCHIVED, PlateState.DISPOSED])
def test_archived_or_disposed_plates_are_finished(state: PlateState) -> None:
    assert make_plate(state=state).is_finished


def test_plates_use_identity_equality_and_are_hashable() -> None:
    first, second = make_plate(), make_plate()

    assert first != second  # same field values, but two distinct physical plates
    assert len({first, second}) == 2


def test_metadata_is_not_shared_between_plates() -> None:
    first, second = make_plate(), make_plate()
    first.metadata["barcode"] = "ABC"

    assert second.metadata == {}
