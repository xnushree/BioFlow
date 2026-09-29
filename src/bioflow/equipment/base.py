"""Base class shared by all equipment, and the PlateHolder interface robots use."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, ClassVar, Generic, Protocol, TypeVar

from bioflow.core.simulation import SimulationContext
from bioflow.core.state_machine import TransitionTable
from bioflow.core.validation import require
from bioflow.domain import EquipmentKind, Plate
from bioflow.equipment.events import EquipmentEvent

S = TypeVar("S", bound=StrEnum)


class Equipment(ABC, Generic[S]):
    """Identity, kind, current state, and event publishing for one piece of equipment.

    State changes go through ``_set_state`` only. That single choke point
    checks every change against the subclass's ``transitions`` table and
    announces it on the bus.
    """

    transitions: ClassVar[TransitionTable[Any]]
    non_operational_states: ClassVar[frozenset[str]] = frozenset()
    fault_state: ClassVar[str] = "FAULT"
    recovery_state: ClassVar[str] = "RECOVERY"

    def __init__(
        self, equipment_id: str, kind: EquipmentKind, context: SimulationContext, initial_state: S
    ) -> None:
        require(bool(equipment_id.strip()), "equipment_id must not be empty")
        self.equipment_id = equipment_id
        self.kind = kind
        self._context = context
        self._state = initial_state
        self._state_before_fault = initial_state
        # Hidden hardware condition, changed only by fault injection.
        self.comms_ok = True  # False: no heartbeats reach the control system

    @property
    def state(self) -> S:
        return self._state

    @property
    def emits_heartbeat(self) -> bool:
        """Whether this equipment's periodic heartbeat currently reaches the control system."""
        return self.comms_ok

    @property
    def is_operational(self) -> bool:
        """False while faulted or recovering; such equipment must not be given new work."""
        return self._state not in self.non_operational_states

    def _set_state(self, new_state: S) -> None:
        if new_state == self._state:
            return
        self.transitions.check(self.equipment_id, self._state, new_state)
        old_state, self._state = self._state, new_state
        self._publish(
            EquipmentEvent.STATE_CHANGED, kind=self.kind, from_state=old_state, to_state=new_state
        )

    # ----------------------------------------------------- fault handling (control)
    def enter_fault(self, state: S | None = None) -> None:
        """Take the equipment out of service (the supervisory system's decision, after detection)."""
        target = state if state is not None else type(self._state)(self.fault_state)
        if self._state != target:
            self._state_before_fault = self._state
        self._set_state(target)

    def begin_recovery(self) -> None:
        self._set_state(type(self._state)(self.recovery_state))

    def complete_recovery(self) -> None:
        """Return to the normal state that matches the equipment's current condition."""
        self._set_state(self._state_after_recovery())

    def _state_after_recovery(self) -> S:
        raise NotImplementedError(f"{type(self).__name__} cannot recover")

    def _publish(self, event_type: EquipmentEvent, **payload: Any) -> None:
        self._context.publish(event_type, self.equipment_id, payload=payload)

    def snapshot(self) -> dict[str, Any]:
        """A plain-dict view of this equipment for telemetry, the API and the dashboard."""
        return {
            "equipment_id": self.equipment_id,
            "kind": self.kind,
            "state": self._state,
            **self._snapshot_details(),
        }

    @abstractmethod
    def _snapshot_details(self) -> dict[str, Any]:
        """Kind-specific fields to include in ``snapshot``."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.equipment_id!r}, state={self._state})"


class PlateHolder(Protocol):
    """Anything a robot can take a plate from or put a plate into."""

    @property
    def equipment_id(self) -> str: ...

    def receive(self, plate: Plate) -> None: ...

    def release(self, plate_id: str) -> Plate: ...
