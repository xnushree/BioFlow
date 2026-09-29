"""Experiment protocols: an immutable recipe of steps shared by many experiments."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from bioflow.core.exceptions import ProtocolError
from bioflow.core.validation import require
from bioflow.domain.conditions import CultureConditions
from bioflow.domain.operation import Operation


@dataclass(frozen=True)
class ProtocolStep:
    """One operation in a protocol.

    ``duration_min`` is mandatory for INCUBATE. For other operations it may be
    left as ``None``, meaning "use the executing equipment's configured time".
    """

    operation: Operation
    duration_min: float | None = None

    def __post_init__(self) -> None:
        require(
            self.operation.allowed_in_protocol,
            f"{self.operation} cannot appear in a protocol; the controller plans it",
            ProtocolError,
        )
        require(
            not (self.operation.requires_duration and self.duration_min is None),
            f"{self.operation} requires a duration_min",
            ProtocolError,
        )
        require(
            self.duration_min is None or self.duration_min > 0,
            f"{self.operation}: duration_min must be positive, got {self.duration_min}",
            ProtocolError,
        )


@dataclass(frozen=True)
class Protocol:
    """A named, validated sequence of steps plus the culture conditions it needs.

    Immutable so one protocol object can be safely shared by many experiments.
    """

    name: str
    cell_type: str
    steps: tuple[ProtocolStep, ...]
    conditions: CultureConditions = field(default_factory=CultureConditions)

    def __post_init__(self) -> None:
        # Accept any sequence from callers but store an immutable tuple.
        object.__setattr__(self, "steps", tuple(self.steps))
        require(bool(self.name.strip()), "Protocol name must not be empty", ProtocolError)
        require(bool(self.cell_type.strip()), f"{self.name}: cell_type must not be empty", ProtocolError)
        require(bool(self.steps), f"{self.name}: protocol has no steps", ProtocolError)
        self._check_terminal_steps(self.steps)

    def _check_terminal_steps(self, steps: Sequence[ProtocolStep]) -> None:
        for index, step in enumerate(steps[:-1]):
            require(
                not step.operation.is_terminal,
                f"{self.name}: step {index + 1} ({step.operation}) ends the workflow "
                "and must be the last step",
                ProtocolError,
            )
