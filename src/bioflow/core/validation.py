"""Small validation helpers shared across the code base."""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from typing import Any

from bioflow.core.exceptions import BioFlowError, ValidationError


def require(
    condition: bool, message: str, error: type[BioFlowError] = ValidationError
) -> None:
    """Raise ``error(message)`` unless ``condition`` holds."""
    if not condition:
        raise error(message)


def is_int(value: Any) -> bool:
    """True for real integers (``bool`` is excluded even though it subclasses int)."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value: Any) -> bool:
    """True for ints and floats, excluding ``bool``."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def suggest(value: str, options: Iterable[str]) -> str:
    """A ``" (did you mean 'x'?)"`` hint for a likely typo, or ``""``."""
    matches = difflib.get_close_matches(value, list(options), n=1)
    return f" (did you mean '{matches[0]}'?)" if matches else ""
