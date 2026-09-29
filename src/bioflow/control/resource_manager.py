"""Central reservation of equipment capacity, and availability queries.

Ownership (single source of truth):
    * Equipment owns *occupancy*: which plates are physically inside it.
    * The ResourceManager owns *reservations*: which plates are on their way.

    free capacity = capacity - occupancy - reservations

A plate must hold a reservation on its destination before a robot starts
moving it, so it can never arrive at a full station. When the plate arrives
(PLATE_RECEIVED on the bus) its reservation is fulfilled automatically.

Waiting is the scheduler's job, not this class's: when capacity frees up the
manager publishes RESOURCE_AVAILABLE, and the scheduler decides, by its own
policy, which waiting task gets it.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from bioflow.core.event_bus import EventBus
from bioflow.core.events import Event
from bioflow.core.exceptions import (
    CapacityExceededError,
    DuplicateAllocationError,
    ResourceUnavailableError,
    SafetyViolationError,
    UnknownEntityError,
)
from bioflow.core.simulation import SimulationContext
from bioflow.domain import EquipmentKind
from bioflow.equipment.base import Equipment
from bioflow.equipment.container import ContainerEquipment
from bioflow.equipment.events import EquipmentEvent
from bioflow.equipment.robot import Robot

logger = logging.getLogger(__name__)

SOURCE_ID = "RESOURCE_MANAGER"


class ResourceEvent(StrEnum):
    RESERVED = "RESOURCE_RESERVED"  # payload: reservation_id, resource_id, plate_id
    RESERVATION_FULFILLED = "RESERVATION_FULFILLED"  # the plate arrived
    RESERVATION_CANCELLED = "RESERVATION_CANCELLED"
    AVAILABLE = "RESOURCE_AVAILABLE"  # payload: resource_id, kind, free


@dataclass(frozen=True)
class Reservation:
    """A claim on one unit of a container's capacity for a plate on its way there."""

    reservation_id: str
    resource_id: str
    plate_id: str
    created_at: float
    task_id: str | None = None


@dataclass(frozen=True)
class ResourceStatus:
    resource_id: str
    kind: EquipmentKind
    capacity: int
    occupancy: int
    reserved: int
    operational: bool

    @property
    def free(self) -> int:
        return self.capacity - self.occupancy - self.reserved

    @property
    def available(self) -> bool:
        return self.operational and self.free > 0


class ResourceManager:
    """Reserves container capacity and answers "what is available?" queries."""

    def __init__(
        self, equipment: Mapping[str, Equipment[Any]], context: SimulationContext, bus: EventBus
    ) -> None:
        self._context = context
        self._containers = {eid: eq for eid, eq in equipment.items() if isinstance(eq, ContainerEquipment)}
        self._robots = {eid: eq for eid, eq in equipment.items() if isinstance(eq, Robot)}
        # Three indexes over the same Reservation objects, updated only in _add/_remove.
        self._by_id: dict[str, Reservation] = {}
        self._by_plate: dict[str, Reservation] = {}
        self._by_resource: dict[str, dict[str, Reservation]] = {eid: {} for eid in self._containers}
        self._ids = itertools.count(1)

        bus.subscribe(EquipmentEvent.PLATE_RECEIVED, self._on_plate_received)
        bus.subscribe(EquipmentEvent.PLATE_RELEASED, self._on_plate_released)
        bus.subscribe(EquipmentEvent.TRANSPORT_COMPLETED, self._on_transport_completed)
        bus.subscribe(EquipmentEvent.STATE_CHANGED, self._on_state_changed)
        self._out_of_service: set[str] = set()

    # ------------------------------------------------------------------ queries
    def status(self, resource_id: str) -> ResourceStatus:
        container = self._container(resource_id)
        return ResourceStatus(
            resource_id=resource_id,
            kind=container.kind,
            capacity=container.capacity,
            occupancy=container.occupancy,
            reserved=len(self._by_resource[resource_id]),
            operational=container.is_operational and resource_id not in self._out_of_service,
        )

    def available(self, kind: EquipmentKind) -> list[str]:
        """IDs of operational containers of ``kind`` with free capacity, in ID order."""
        return sorted(
            eid for eid, eq in self._containers.items() if eq.kind is kind and self.status(eid).available
        )

    def available_robots(self) -> list[str]:
        return sorted(
            eid for eid, robot in self._robots.items() if robot.is_idle and eid not in self._out_of_service
        )

    # --------------------------------------------------------- service status
    def take_out_of_service(self, equipment_id: str) -> None:
        """Stop offering this equipment for new work (e.g. unreachable, or awaiting maintenance).

        Unlike a fault *state*, this leaves the equipment's own state machine alone, so work
        already in progress (a robot finishing its current trip) can complete normally.
        """
        self._out_of_service.add(equipment_id)

    def return_to_service(self, equipment_id: str) -> None:
        if equipment_id in self._out_of_service:
            self._out_of_service.discard(equipment_id)
            self._announce_any(equipment_id)

    def in_service(self, equipment_id: str) -> bool:
        return equipment_id not in self._out_of_service

    def reservation_for_plate(self, plate_id: str) -> Reservation | None:
        return self._by_plate.get(plate_id)

    def reservations_at(self, resource_id: str) -> list[Reservation]:
        self._container(resource_id)
        return list(self._by_resource[resource_id].values())

    def snapshot(self) -> list[dict[str, Any]]:
        rows = []
        for eid in sorted(self._containers):
            s = self.status(eid)
            rows.append({
                "resource_id": eid, "kind": s.kind, "capacity": s.capacity, "occupancy": s.occupancy,
                "reserved": s.reserved, "free": s.free, "operational": s.operational,
            })
        return rows

    # ----------------------------------------------------------------- commands
    def reserve(self, resource_id: str, plate_id: str, task_id: str | None = None) -> Reservation:
        """Claim one unit of ``resource_id`` for ``plate_id``, which is about to be sent there.

        Raises:
            UnknownEntityError: ``resource_id`` is not a plate-holding resource.
            ResourceUnavailableError: the equipment is faulted or recovering.
            DuplicateAllocationError: the plate already has a reservation, or is already there.
            CapacityExceededError: no free capacity once existing reservations are counted.
        """
        status = self.status(resource_id)
        if not status.operational:
            raise ResourceUnavailableError(
                resource_id, f"not operational ({self._containers[resource_id].state})"
            )
        existing = self._by_plate.get(plate_id)
        if existing is not None:
            raise DuplicateAllocationError(existing.resource_id, plate_id)
        if self._containers[resource_id].holds(plate_id):
            raise DuplicateAllocationError(resource_id, plate_id)
        if status.free <= 0:
            raise CapacityExceededError(resource_id, status.capacity)

        reservation = Reservation(f"RSV{next(self._ids):06d}", resource_id, plate_id, self._context.now, task_id)
        self._add(reservation)
        self._publish(ResourceEvent.RESERVED, reservation)
        return reservation

    def cancel(self, reservation_id: str) -> bool:
        """Withdraw a reservation (e.g. the transport was aborted). False if it is not active."""
        reservation = self._by_id.get(reservation_id)
        if reservation is None:
            return False
        self._remove(reservation)
        self._publish(ResourceEvent.RESERVATION_CANCELLED, reservation)
        self._announce_available(reservation.resource_id)
        return True

    # ------------------------------------------------------------ bus handlers
    def _on_plate_received(self, event: Event) -> None:
        resource_id, plate_id = event.source, event.payload["plate_id"]
        if resource_id not in self._containers:
            return
        reservation = self._by_plate.get(plate_id)
        if reservation is not None:
            if reservation.resource_id != resource_id:
                raise SafetyViolationError(
                    resource_id, f"{plate_id} arrived here but was reserved for {reservation.resource_id}"
                )
            self._remove(reservation)
            self._publish(ResourceEvent.RESERVATION_FULFILLED, reservation)
        elif self.status(resource_id).free < 0:
            raise SafetyViolationError(
                resource_id, f"unreserved arrival of {plate_id} took capacity reserved for other plates"
            )

    def _on_plate_released(self, event: Event) -> None:
        if event.source in self._containers:
            self._announce_available(event.source)

    def _on_transport_completed(self, event: Event) -> None:
        self._announce_any(event.source)

    def _on_state_changed(self, event: Event) -> None:
        """Equipment coming back from a fault/recovery state offers its capacity again."""
        equipment = self._containers.get(event.source) or self._robots.get(event.source)
        if equipment is not None and equipment.is_operational and \
                event.payload["from_state"] in equipment.non_operational_states:
            self._announce_any(event.source)

    def _announce_any(self, equipment_id: str) -> None:
        if equipment_id in self._containers:
            self._announce_available(equipment_id)
            return
        robot = self._robots.get(equipment_id)
        if robot is not None and robot.is_idle and equipment_id not in self._out_of_service:
            self._context.publish(
                ResourceEvent.AVAILABLE, SOURCE_ID, target=equipment_id,
                payload={"resource_id": equipment_id, "kind": robot.kind, "free": 1},
            )

    # ------------------------------------------------------------------ helpers
    def _container(self, resource_id: str) -> ContainerEquipment[Any]:
        try:
            return self._containers[resource_id]
        except KeyError:
            raise UnknownEntityError(resource_id, "plate-holding resource") from None

    def _add(self, reservation: Reservation) -> None:
        self._by_id[reservation.reservation_id] = reservation
        self._by_plate[reservation.plate_id] = reservation
        self._by_resource[reservation.resource_id][reservation.reservation_id] = reservation

    def _remove(self, reservation: Reservation) -> None:
        del self._by_id[reservation.reservation_id]
        del self._by_plate[reservation.plate_id]
        del self._by_resource[reservation.resource_id][reservation.reservation_id]

    def _announce_available(self, resource_id: str) -> None:
        status = self.status(resource_id)
        if status.available:
            self._context.publish(
                ResourceEvent.AVAILABLE, SOURCE_ID, target=resource_id,
                payload={"resource_id": resource_id, "kind": status.kind, "free": status.free},
            )

    def _publish(self, event_type: ResourceEvent, reservation: Reservation) -> None:
        logger.debug("%s %s -> %s", event_type, reservation.plate_id, reservation.resource_id)
        self._context.publish(
            event_type, SOURCE_ID, target=reservation.resource_id,
            payload={
                "reservation_id": reservation.reservation_id,
                "resource_id": reservation.resource_id,
                "plate_id": reservation.plate_id,
                "task_id": reservation.task_id,
            },
        )
