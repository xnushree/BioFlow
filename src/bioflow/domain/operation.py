"""The operations a plate can undergo."""

from __future__ import annotations

from enum import StrEnum


class Operation(StrEnum):
    """Something that happens to a plate.

    ``TRANSPORT`` is special: it is never written in a protocol. The controller
    inserts transport tasks itself whenever a plate must move between equipment.
    """

    INCUBATE = "INCUBATE"
    MEDIA_EXCHANGE = "MEDIA_EXCHANGE"
    IMAGE = "IMAGE"
    ARCHIVE = "ARCHIVE"
    DISPOSE = "DISPOSE"
    TRANSPORT = "TRANSPORT"

    @property
    def allowed_in_protocol(self) -> bool:
        return self is not Operation.TRANSPORT

    @property
    def is_terminal(self) -> bool:
        """Terminal operations end a plate's workflow, so they may only be the last step."""
        return self in (Operation.ARCHIVE, Operation.DISPOSE)

    @property
    def requires_duration(self) -> bool:
        """Incubation length is experiment-specific; other durations come from equipment config."""
        return self is Operation.INCUBATE
