"""Notifications that equipment publishes on the event bus."""

from enum import StrEnum


class EquipmentEvent(StrEnum):
    STATE_CHANGED = "EQUIPMENT_STATE_CHANGED"  # payload: kind, from_state, to_state
    PLATE_RECEIVED = "PLATE_RECEIVED"  # payload: plate_id
    PLATE_RELEASED = "PLATE_RELEASED"  # payload: plate_id
    PROCESSING_STARTED = "PROCESSING_STARTED"  # payload: plate_id, operation, duration_min
    PROCESSING_COMPLETED = "PROCESSING_COMPLETED"  # payload: plate_id, operation
    PLATE_ARCHIVED = "PLATE_ARCHIVED"  # payload: plate_id
    TRANSPORT_STARTED = "TRANSPORT_STARTED"  # payload: plate_id, source, destination
    PLATE_PICKED = "PLATE_PICKED"  # payload: plate_id, source
    PLATE_PLACED = "PLATE_PLACED"  # payload: plate_id, destination
    TRANSPORT_COMPLETED = "TRANSPORT_COMPLETED"  # payload: plate_id, source, destination
