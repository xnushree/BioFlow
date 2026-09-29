"""Tests for protocol validation: structure, types, domain rules, and error locations."""

import copy
from typing import Any

import pytest

from bioflow.domain import Operation
from bioflow.protocols import validate_protocol

VALID: dict[str, Any] = {
    "protocol": "basic",
    "cell_type": "HEK293",
    "steps": [
        {"operation": "INCUBATE", "duration_min": 720},
        {"operation": "MEDIA_EXCHANGE"},
        {"operation": "IMAGE", "duration_min": 12.5},
        {"operation": "ARCHIVE"},
    ],
}


def changed(**top_level: Any) -> dict[str, Any]:
    data = copy.deepcopy(VALID)
    data.update(top_level)
    return data


def with_steps(*steps: dict[str, Any]) -> dict[str, Any]:
    return changed(steps=list(steps))


def issue_strings(data: Any) -> list[str]:
    result = validate_protocol(data)
    assert result.protocol is None
    return [str(issue) for issue in result.issues]


def test_valid_protocol_builds_domain_object() -> None:
    result = validate_protocol(VALID)

    assert result.is_valid
    protocol = result.protocol
    assert protocol is not None
    assert [s.operation for s in protocol.steps] == [
        Operation.INCUBATE, Operation.MEDIA_EXCHANGE, Operation.IMAGE, Operation.ARCHIVE
    ]
    assert protocol.steps[1].duration_min is None  # station default applies later
    assert protocol.conditions.temperature_c == 37.0  # default conditions


def test_custom_conditions_are_applied() -> None:
    result = validate_protocol(changed(conditions={"temperature_c": 32.0, "co2_pct": 8}))

    assert result.protocol is not None
    assert (result.protocol.conditions.temperature_c, result.protocol.conditions.co2_pct) == (32.0, 8)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ([1, 2], "document: expected a mapping of protocol fields"),
        ({k: v for k, v in VALID.items() if k != "steps"}, "document: missing required key 'steps'"),
        (changed(protcol="x"), "document: unknown key 'protcol' (did you mean 'protocol'?)"),
        (changed(protocol="  "), "protocol: expected non-empty text"),
        (changed(cell_type=42), "cell_type: expected non-empty text"),
        (changed(description=["x"]), "description: expected text"),
        (changed(steps=[]), "steps: expected a non-empty list of steps"),
        (changed(steps="INCUBATE"), "steps: expected a non-empty list of steps"),
        (changed(conditions=[37]), "conditions: expected a mapping"),
        (changed(conditions={"temp": 37}), "conditions: unknown key 'temp'"),
        (changed(conditions={"co2_pct": "high"}), "conditions.co2_pct: expected a number, got 'high'"),
        (changed(conditions={"co2_pct": 150}), "conditions: co2_pct must be within 0-100, got 150"),
    ],
)
def test_document_problems_are_located(data: Any, expected: str) -> None:
    assert expected in issue_strings(data)


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        ("INCUBATE", "step 1: expected a mapping with an 'operation'"),
        ({"duration_min": 5}, "step 1: missing required key 'operation'"),
        ({"operation": "IMAGE", "duraton_min": 5}, "step 1: unknown key 'duraton_min' (did you mean 'duration_min'?)"),
        ({"operation": "image"}, "step 1.operation: unknown operation 'image' (did you mean 'IMAGE'?)"),
        ({"operation": "IMAGE", "duration_min": "10"}, "step 1.duration_min: expected a number, got '10'"),
        ({"operation": "IMAGE", "duration_min": True}, "step 1.duration_min: expected a number, got True"),
        ({"operation": "INCUBATE"}, "step 1: INCUBATE requires a duration_min"),
        ({"operation": "IMAGE", "duration_min": -1}, "step 1: IMAGE: duration_min must be positive, got -1"),
        ({"operation": "TRANSPORT"}, "step 1: TRANSPORT cannot appear in a protocol; the controller plans it"),
    ],
)
def test_step_problems_are_located(step: Any, expected: str) -> None:
    issues = issue_strings(with_steps(step, {"operation": "ARCHIVE"}))

    assert any(issue.startswith(expected) for issue in issues), issues


def test_protocol_level_rules_come_from_the_domain() -> None:
    issues = issue_strings(with_steps({"operation": "ARCHIVE"}, {"operation": "IMAGE"}))

    assert issues == ["steps: step 1 (ARCHIVE) ends the workflow and must be the last step"]


def test_missing_final_destination_is_reported() -> None:
    issues = issue_strings(with_steps({"operation": "INCUBATE", "duration_min": 60}))

    assert len(issues) == 1 and "must end with ARCHIVE or DISPOSE" in issues[0]


def test_all_problems_are_reported_together() -> None:
    data = changed(
        cell_typ="HEK293",
        steps=[{"operation": "INCUBATE"}, {"operation": "IMAGEE"}, {"operation": "ARCHIVE"}],
    )
    data.pop("cell_type")

    issues = issue_strings(data)

    assert len(issues) == 4
    assert [issue.split(":")[0] for issue in issues] == ["document", "document", "step 1", "step 2.operation"]
