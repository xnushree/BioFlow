"""Base class shared by all equipment, and the PlateHolder interface robots use."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Generic, Protocol, TypeVar

from bioflow.core.simulation import SimulationContext
from bioflow.core.validation import require
from bioflow.domain import EquipmentKind, Plate
from bioflow.equipment.events import EquipmentEvent

S = TypeVar("S", bound=StrEnum)


class Equipment(ABC, Generic[S]):
    """Identity, kind, current state, and event publishing for one piece of equipment.

    State changes go through ``_set_state`` only. That single choke point is
    where Phase 6 plugs in the transition rules, and it guarantees every state
    change is announced on the bus.
    """

    def __init__(
        self, equipment_id: str, kind: EquipmentKind, context: SimulationContext, initial_state: S
    ) -> None:
        require(bool(equipment_id.strip()), "equipment_id must not be empty")
        self.equipment_id = equipment_id
        self.kind = kind
        self._context = context
        self._state = initial_state

    @property
    def state(self) -> S:
        return self._state

    def _set_state(self, new_state: S) -> None:
        if new_state == self._state:
            return
        old_state, self._state = self._state, new_state
        self._publish(
            EquipmentEvent.STATE_CHANGED, kind=self.kind, from_state=old_state, to_state=new_state
        )

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
