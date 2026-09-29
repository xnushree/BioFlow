"""Capacity-limited plate bookkeeping, reused by every plate-holding equipment type."""

from __future__ import annotations

from bioflow.core.exceptions import CapacityExceededError, DuplicateAllocationError, UnknownEntityError
from bioflow.core.validation import require
from bioflow.domain import Plate


class PlateSlots:
    """The set of plates physically inside one piece of equipment."""

    def __init__(self, owner_id: str, capacity: int) -> None:
        require(capacity >= 1, f"{owner_id}: capacity must be >= 1, got {capacity}")
        self.owner_id = owner_id
        self.capacity = capacity
        self._plates: dict[str, Plate] = {}  # insertion-ordered

    @property
    def count(self) -> int:
        return len(self._plates)

    @property
    def free(self) -> int:
        return self.capacity - self.count

    @property
    def is_full(self) -> bool:
        return self.count >= self.capacity

    @property
    def plate_ids(self) -> tuple[str, ...]:
        return tuple(self._plates)

    def __contains__(self, plate_id: object) -> bool:
        return plate_id in self._plates

    def add(self, plate: Plate) -> None:
        if plate.plate_id in self._plates:
            raise DuplicateAllocationError(self.owner_id, plate.plate_id)
        if self.is_full:
            raise CapacityExceededError(self.owner_id, self.capacity)
        self._plates[plate.plate_id] = plate

    def get(self, plate_id: str) -> Plate:
        try:
            return self._plates[plate_id]
        except KeyError:
            raise UnknownEntityError(plate_id, f"plate in {self.owner_id}") from None

    def remove(self, plate_id: str) -> Plate:
        plate = self.get(plate_id)
        del self._plates[plate_id]
        return plate
