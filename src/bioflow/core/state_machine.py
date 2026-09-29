"""Finite-state-machine rules.

A state machine here is two things kept apart on purpose:

    * the **current state**, stored once on the entity itself (``plate.state``,
      ``robot.state``), so there is a single source of truth, and
    * a **TransitionTable**, an immutable, shared description of which moves
      are legal for that kind of entity.

Every state change is checked against the table; illegal moves raise
``InvalidTransitionError`` and leave the state untouched.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, ClassVar, Generic, TypeVar

from bioflow.core.exceptions import InvalidTransitionError

S = TypeVar("S", bound=StrEnum)


class TransitionTable(Generic[S]):
    """The legal transitions between the states of one ``StrEnum``."""

    def __init__(self, state_type: type[S], allowed: Mapping[S, frozenset[S]]) -> None:
        self.state_type = state_type
        self._allowed = dict(allowed)

    @classmethod
    def build(
        cls,
        state_type: type[S],
        edges: Mapping[S, Iterable[S]],
        *,
        from_any: Iterable[S] = (),
        terminal: Iterable[S] = (),
    ) -> TransitionTable[S]:
        """Build and sanity-check a table.

        Args:
            state_type: The enum whose members are the states.
            edges: For each non-terminal state, the states it may move to.
            from_any: States reachable from *every* non-terminal state
                (e.g. FAULT: "any state can fail").
            terminal: States with no way out (e.g. ARCHIVED).

        Raises:
            ValueError: If the definition is inconsistent. This is a
                programming error, detected at import time.
        """
        terminal_set = frozenset(terminal)
        from_any_set = frozenset(from_any)
        all_states = frozenset(state_type)

        overlap = terminal_set & set(edges)
        if overlap:
            raise ValueError(f"{state_type.__name__}: terminal states have outgoing edges: {sorted(overlap)}")
        missing = all_states - set(edges) - terminal_set
        if missing:
            raise ValueError(f"{state_type.__name__}: no transitions defined for {sorted(missing)}")

        allowed: dict[S, frozenset[S]] = {state: frozenset() for state in terminal_set}
        for state, targets in edges.items():
            targets_set = frozenset(targets) | (from_any_set - {state})
            if state in targets_set:
                raise ValueError(f"{state_type.__name__}: self-transition {state} -> {state}")
            unknown = targets_set - all_states
            if unknown:
                raise ValueError(f"{state_type.__name__}: unknown target states {sorted(unknown)}")
            allowed[state] = targets_set
        return cls(state_type, allowed)

    def can(self, from_state: S, to_state: S) -> bool:
        return to_state in self._allowed[from_state]

    def targets(self, from_state: S) -> frozenset[S]:
        return self._allowed[from_state]

    def is_terminal(self, state: S) -> bool:
        return not self._allowed[state]

    def check(self, entity_id: str, from_state: S, to_state: S) -> None:
        """Raise ``InvalidTransitionError`` unless ``from_state -> to_state`` is legal."""
        if not self.can(from_state, to_state):
            raise InvalidTransitionError(entity_id, str(from_state), str(to_state))


class TransitionGuard:
    """Mixin for (dataclass) entities: validates every assignment to one state attribute.

    The first assignment (in ``__init__``) is accepted as-is, so an entity can be
    created in any state, e.g. when restored from the database. Assigning the
    current value again is a harmless no-op.
    """

    _state_attr: ClassVar[str]
    _id_attr: ClassVar[str]
    _transitions: ClassVar[TransitionTable[Any]]

    def __setattr__(self, name: str, value: Any) -> None:
        if name == self._state_attr and name in self.__dict__:
            current = self.__dict__[name]
            if value != current:
                self._transitions.check(getattr(self, self._id_attr), current, value)
        super().__setattr__(name, value)
