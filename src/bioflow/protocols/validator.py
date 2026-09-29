"""Validate raw protocol data (already loaded from YAML) and build a Protocol.

Two layers of checking, with no rule written twice:

1. **Document checks (here):** is it a mapping, are required keys present,
   are there unknown keys (with "did you mean" hints), are values the right
   type, is the operation name real?
2. **Domain rules (in bioflow.domain):** INCUBATE needs a duration, durations
   are positive, ARCHIVE/DISPOSE must be last, ... The validator builds the
   domain objects and turns anything they reject into a located issue.

All problems are collected and reported together rather than one per run.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from typing import Any

from bioflow.core.exceptions import BioFlowError, ProtocolError
from bioflow.domain import CultureConditions, Operation, Protocol, ProtocolStep

REQUIRED_KEYS = ("protocol", "cell_type", "steps")
OPTIONAL_KEYS = ("description", "conditions")
STEP_KEYS = ("operation", "duration_min", "synchronize")
CONDITION_KEYS = tuple(f.name for f in fields(CultureConditions))
PROTOCOL_OPERATIONS = tuple(op.value for op in Operation if op.allowed_in_protocol)
ALL_OPERATIONS = frozenset(op.value for op in Operation)


@dataclass(frozen=True)
class ValidationIssue:
    """One problem, with where it is: e.g. ``step 3.duration_min``."""

    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


class ProtocolValidationError(ProtocolError):
    """A protocol document has one or more problems."""

    def __init__(self, source: str, issues: list[ValidationIssue]) -> None:
        self.source = source
        self.issues = issues
        lines = "\n".join(f"  - {issue}" for issue in issues)
        super().__init__(f"{source}: {len(issues)} problem(s)\n{lines}")


@dataclass(frozen=True)
class ValidationResult:
    protocol: Protocol | None
    issues: list[ValidationIssue]

    @property
    def is_valid(self) -> bool:
        return not self.issues


def validate_protocol(data: Any) -> ValidationResult:
    """Check ``data`` and, if it is valid, build the Protocol."""
    issues: list[ValidationIssue] = []
    if not isinstance(data, Mapping):
        return ValidationResult(None, [ValidationIssue("document", "expected a mapping of protocol fields")])

    _check_keys(data, REQUIRED_KEYS, REQUIRED_KEYS + OPTIONAL_KEYS, "document", issues)
    name = _non_empty_string(data, "protocol", issues)
    cell_type = _non_empty_string(data, "cell_type", issues)
    if "description" in data and not isinstance(data["description"], str):
        issues.append(ValidationIssue("description", "expected text"))
    conditions = _parse_conditions(data.get("conditions"), issues)
    steps = _parse_steps(data.get("steps"), issues)

    if issues or name is None or cell_type is None or steps is None or conditions is None:
        return ValidationResult(None, issues)
    try:
        protocol = Protocol(name=name, cell_type=cell_type, steps=tuple(steps), conditions=conditions)
    except BioFlowError as error:  # protocol-level domain rules, e.g. ARCHIVE must be last
        return ValidationResult(None, [ValidationIssue("steps", _strip_prefix(str(error), name))])
    return ValidationResult(protocol, [])


def _parse_conditions(raw: Any, issues: list[ValidationIssue]) -> CultureConditions | None:
    if raw is None:
        return CultureConditions()
    if not isinstance(raw, Mapping):
        issues.append(ValidationIssue("conditions", "expected a mapping"))
        return None
    before = len(issues)
    _check_keys(raw, (), CONDITION_KEYS, "conditions", issues)
    for key, value in raw.items():
        if key in CONDITION_KEYS and not _is_number(value):
            issues.append(ValidationIssue(f"conditions.{key}", f"expected a number, got {value!r}"))
    if len(issues) > before:
        return None
    try:
        return CultureConditions(**raw)
    except BioFlowError as error:
        issues.append(ValidationIssue("conditions", str(error)))
        return None


def _parse_steps(raw: Any, issues: list[ValidationIssue]) -> list[ProtocolStep] | None:
    if raw is None:
        return None  # already reported as a missing required key
    if not isinstance(raw, list) or not raw:
        issues.append(ValidationIssue("steps", "expected a non-empty list of steps"))
        return None
    steps: list[ProtocolStep] = []
    for number, raw_step in enumerate(raw, start=1):
        step = _parse_step(raw_step, f"step {number}", issues)
        if step is not None:
            steps.append(step)
    return steps if len(steps) == len(raw) else None


def _parse_step(raw: Any, location: str, issues: list[ValidationIssue]) -> ProtocolStep | None:
    if not isinstance(raw, Mapping):
        issues.append(ValidationIssue(location, "expected a mapping with an 'operation'"))
        return None
    before = len(issues)
    _check_keys(raw, ("operation",), STEP_KEYS, location, issues)

    operation = raw.get("operation")
    # Real-but-forbidden operations (TRANSPORT) fall through to the domain rule, which explains why.
    if "operation" in raw and operation not in ALL_OPERATIONS:
        issues.append(ValidationIssue(
            f"{location}.operation",
            f"unknown operation {operation!r}{_suggest(str(operation).upper(), PROTOCOL_OPERATIONS)}; "
            f"expected one of {', '.join(PROTOCOL_OPERATIONS)}",
        ))
    duration = raw.get("duration_min")
    if duration is not None and not _is_number(duration):
        issues.append(ValidationIssue(f"{location}.duration_min", f"expected a number, got {duration!r}"))
    synchronize = raw.get("synchronize", False)
    if not isinstance(synchronize, bool):
        issues.append(ValidationIssue(f"{location}.synchronize", f"expected true or false, got {synchronize!r}"))
    if len(issues) > before:
        return None
    try:
        return ProtocolStep(Operation(operation), duration, synchronize)
    except BioFlowError as error:  # step-level domain rules, e.g. INCUBATE needs a duration
        issues.append(ValidationIssue(location, str(error)))
        return None


def _check_keys(
    raw: Mapping[str, Any], required: Iterable[str], allowed: Iterable[str],
    location: str, issues: list[ValidationIssue],
) -> None:
    allowed = tuple(allowed)
    for key in required:
        if key not in raw:
            issues.append(ValidationIssue(location, f"missing required key '{key}'"))
    for key in raw:
        if key not in allowed:
            issues.append(ValidationIssue(location, f"unknown key '{key}'{_suggest(str(key), allowed)}"))


def _non_empty_string(data: Mapping[str, Any], key: str, issues: list[ValidationIssue]) -> str | None:
    if key not in data:
        return None  # reported by _check_keys
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        issues.append(ValidationIssue(key, "expected non-empty text"))
        return None
    return value


def _suggest(value: str, options: Iterable[str]) -> str:
    matches = difflib.get_close_matches(value, list(options), n=1)
    return f" (did you mean '{matches[0]}'?)" if matches else ""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _strip_prefix(message: str, name: str) -> str:
    """Domain messages start with the protocol name; the location already identifies it."""
    prefix = f"{name}: "
    return message[len(prefix):] if message.startswith(prefix) else message
