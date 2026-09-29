"""Small invariant-checking helper shared by domain objects."""

from __future__ import annotations

from bioflow.core.exceptions import BioFlowError, ValidationError


def require(
    condition: bool, message: str, error: type[BioFlowError] = ValidationError
) -> None:
    """Raise ``error(message)`` unless ``condition`` holds."""
    if not condition:
        raise error(message)
