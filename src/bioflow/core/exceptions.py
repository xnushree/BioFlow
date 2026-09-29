"""Exception hierarchy for BioFlow-X.

Every error raised by the platform derives from ``BioFlowError`` so callers
(API, dashboard, scenario runner) can catch platform failures in one place
while letting genuine programming bugs (TypeError, KeyError, ...) propagate.

Exceptions that describe a safety violation carry structured attributes, not
just a message, so the fault manager and telemetry can act on them without
parsing strings.
"""

from __future__ import annotations


class BioFlowError(Exception):
    """Base class for all BioFlow-X errors."""


class ConfigurationError(BioFlowError):
    """A laboratory, equipment, or scheduling configuration is missing or invalid."""


class ValidationError(BioFlowError):
    """A domain object was built with values that violate its invariants."""


class ProtocolError(BioFlowError):
    """An experiment protocol failed to parse or validate."""


class SimulationError(BioFlowError):
    """The simulation engine was used incorrectly (e.g. an event scheduled in the past)."""


class UnknownEntityError(BioFlowError):
    """A command referenced an equipment, plate, or task ID that does not exist."""

    def __init__(self, entity_id: str, entity_kind: str = "entity") -> None:
        self.entity_id = entity_id
        self.entity_kind = entity_kind
        super().__init__(f"Unknown {entity_kind}: {entity_id!r}")


class InvalidTransitionError(BioFlowError):
    """A state machine was asked to make a transition its rules do not allow."""

    def __init__(self, entity_id: str, from_state: str, to_state: str) -> None:
        self.entity_id = entity_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"{entity_id}: invalid transition {from_state} -> {to_state}"
        )


class SafetyViolationError(BioFlowError):
    """A command was refused because executing it would be physically unsafe,
    e.g. removing a plate from a station while it is still being processed."""

    def __init__(self, equipment_id: str, message: str) -> None:
        self.equipment_id = equipment_id
        super().__init__(f"{equipment_id}: {message}")


class ResourceError(BioFlowError):
    """Base class for resource-allocation failures."""

    def __init__(self, resource_id: str, message: str) -> None:
        self.resource_id = resource_id
        super().__init__(f"{resource_id}: {message}")


class ResourceUnavailableError(ResourceError):
    """The resource exists but cannot be allocated now (faulted, offline, reserved)."""


class CapacityExceededError(ResourceError):
    """Allocating would exceed the resource's configured capacity."""

    def __init__(self, resource_id: str, capacity: int) -> None:
        self.capacity = capacity
        super().__init__(resource_id, f"capacity {capacity} exceeded")


class DuplicateAllocationError(ResourceError):
    """The same holder tried to allocate a resource it already holds."""

    def __init__(self, resource_id: str, holder_id: str) -> None:
        self.holder_id = holder_id
        super().__init__(resource_id, f"already allocated to {holder_id}")


class UnrecoverableFaultError(BioFlowError):
    """Recovery was attempted and failed; the system must move to a safe state."""

    def __init__(self, fault_id: str, reason: str) -> None:
        self.fault_id = fault_id
        self.reason = reason
        super().__init__(f"Fault {fault_id} is unrecoverable: {reason}")
