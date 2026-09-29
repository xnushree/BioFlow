"""Tests for Experiment."""

import pytest

from bioflow.core.exceptions import ValidationError
from bioflow.domain import Experiment, ExperimentStatus, PlateState, Protocol


def test_new_experiment_is_submitted(basic_protocol: Protocol) -> None:
    experiment = Experiment("EXP001", basic_protocol, plate_count=3, submitted_at=0.0)

    assert experiment.status is ExperimentStatus.SUBMITTED


def test_create_plates_assigns_sequential_ids_and_protocol_attributes(
    basic_protocol: Protocol,
) -> None:
    experiment = Experiment("EXP001", basic_protocol, plate_count=3, submitted_at=5.0)

    plates = experiment.create_plates()

    assert [p.plate_id for p in plates] == ["EXP001-P001", "EXP001-P002", "EXP001-P003"]
    for plate in plates:
        assert plate.experiment_id == "EXP001"
        assert plate.cell_type == basic_protocol.cell_type
        assert plate.conditions is basic_protocol.conditions
        assert plate.created_at == 5.0
        assert plate.state is PlateState.CREATED


def test_is_late_only_after_deadline(basic_protocol: Protocol) -> None:
    experiment = Experiment("EXP001", basic_protocol, 1, submitted_at=0.0, deadline=100.0)

    assert not experiment.is_late(100.0)
    assert experiment.is_late(100.1)


def test_no_deadline_is_never_late(basic_protocol: Protocol) -> None:
    assert not Experiment("EXP001", basic_protocol, 1, submitted_at=0.0).is_late(1e9)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"experiment_id": ""},
        {"plate_count": 0},
        {"submitted_at": -1.0},
        {"priority": -1},
        {"submitted_at": 50.0, "deadline": 50.0},
    ],
)
def test_rejects_invalid_fields(basic_protocol: Protocol, kwargs: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "experiment_id": "EXP001",
        "protocol": basic_protocol,
        "plate_count": 1,
        "submitted_at": 0.0,
    }
    fields.update(kwargs)

    with pytest.raises(ValidationError):
        Experiment(**fields)  # type: ignore[arg-type]
