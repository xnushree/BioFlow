"""Load protocol YAML files into validated Protocol objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from bioflow.domain import Protocol
from bioflow.protocols.validator import ProtocolValidationError, ValidationIssue, validate_protocol


def load_protocol(path: Path) -> Protocol:
    """Read, validate and build the protocol in ``path``.

    Raises:
        ProtocolValidationError: listing every problem found, with its location.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ProtocolValidationError(str(path), [ValidationIssue("file", "not found")]) from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f"line {mark.line + 1}" if mark is not None else "document"
        problem = getattr(error, "problem", None) or "invalid YAML"
        raise ProtocolValidationError(path.name, [ValidationIssue(location, problem)]) from error
    return parse_protocol(data, source=path.name)


def parse_protocol(data: Any, source: str = "<data>") -> Protocol:
    """Validate already-loaded data and build the Protocol."""
    result = validate_protocol(data)
    if result.protocol is None:
        raise ProtocolValidationError(source, result.issues)
    return result.protocol
