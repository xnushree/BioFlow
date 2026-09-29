"""Look up scheduling policies by name (as used in scenario files and the CLI)."""

from __future__ import annotations

from bioflow.core.exceptions import ConfigurationError
from bioflow.core.validation import suggest
from bioflow.scheduling.base_scheduler import Scheduler
from bioflow.scheduling.fifo_scheduler import FifoScheduler

SCHEDULERS: dict[str, type[Scheduler]] = {
    FifoScheduler.name: FifoScheduler,
}


def create_scheduler(name: str) -> Scheduler:
    try:
        return SCHEDULERS[name]()
    except KeyError:
        raise ConfigurationError(
            f"Unknown scheduler {name!r}{suggest(name, SCHEDULERS)}; available: {', '.join(sorted(SCHEDULERS))}"
        ) from None
