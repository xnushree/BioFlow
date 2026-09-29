"""Look up scheduling policies by name (as used in scenario files and the CLI)."""

from __future__ import annotations

from collections.abc import Callable

from bioflow.core.exceptions import ConfigurationError
from bioflow.core.validation import suggest
from bioflow.scheduling.base_scheduler import Scheduler
from bioflow.scheduling.config import SchedulingConfig
from bioflow.scheduling.cost_scheduler import CostScheduler
from bioflow.scheduling.deadline_scheduler import DeadlineScheduler
from bioflow.scheduling.fifo_scheduler import FifoScheduler
from bioflow.scheduling.priority_scheduler import PriorityScheduler

SCHEDULERS: dict[str, Callable[[SchedulingConfig], Scheduler]] = {
    FifoScheduler.name: lambda config: FifoScheduler(),
    PriorityScheduler.name: lambda config: PriorityScheduler(),
    DeadlineScheduler.name: lambda config: DeadlineScheduler(),
    CostScheduler.name: lambda config: CostScheduler(config.cost),
}


def create_scheduler(name: str, config: SchedulingConfig | None = None) -> Scheduler:
    """A fresh scheduler instance (policies may keep per-run memory, so never share one)."""
    try:
        factory = SCHEDULERS[name]
    except KeyError:
        raise ConfigurationError(
            f"Unknown scheduler {name!r}{suggest(name, SCHEDULERS)}; available: {', '.join(sorted(SCHEDULERS))}"
        ) from None
    return factory(config or SchedulingConfig())
