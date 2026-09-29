"""Cost-based dispatching: score every (task, destination, robot) option and pick the lowest.

    J =  w_travel    * travel_min        robot -> plate location -> destination
       + w_switching * switch            1 if a processing station last handled a different cell type
       - w_delay     * waited_min        time the task has been READY
       - w_idle      * dest_idle_min     time since the destination was last assigned work
       - w_deadline  * critical_ratio    remaining work / time left before the deadline

Travel and switching are costs. Waiting, idle equipment and deadline pressure
are urgency, so they *lower* J and pull those tasks forward. All weights are
non-negative and configurable (configs/scheduling.yaml).

This is a greedy, one-decision-at-a-time rule, not a global optimiser: it
scores the options available *now*. Whether it beats simpler rules depends on
the workload, which is exactly what the benchmarks measure.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from bioflow.domain import EQUIPMENT_FOR_OPERATION, EquipmentKind, Task
from bioflow.scheduling.base_scheduler import Scheduler, SchedulingView
from bioflow.scheduling.config import CostSettings

# Only single-plate processing stations pay a switching cost; incubators hold mixed plates.
SWITCHING_KINDS = frozenset({EquipmentKind.MEDIA_STATION, EquipmentKind.IMAGING_STATION})


@dataclass(frozen=True)
class CostBreakdown:
    """The individual terms of J for one option, before weighting. Useful for explaining decisions."""

    travel_min: float
    switch: float
    waited_min: float
    dest_idle_min: float
    critical_ratio: float

    def total(self, settings: CostSettings) -> float:
        w = settings.weights
        return (
            w.travel * self.travel_min
            + w.switching * self.switch
            - w.delay * self.waited_min
            - w.idle * self.dest_idle_min
            - w.deadline * self.critical_ratio
        )


class CostScheduler(Scheduler):
    name = "cost"

    def __init__(self, settings: CostSettings | None = None) -> None:
        self.settings = settings or CostSettings()
        # Policy memory, updated only when the dispatcher accepts a destination choice.
        self._last_cell_type: dict[str, str] = {}
        self._last_assigned_at: dict[str, float] = {}

    # ------------------------------------------------------------- decisions
    def order(self, ready: Sequence[Task], view: SchedulingView) -> list[Task]:
        """Lowest best-achievable J first; tasks with no feasible option go last."""
        scored = []
        for index, task in enumerate(ready):
            breakdown = self.explain(task, view)
            cost = math.inf if breakdown is None else breakdown.total(self.settings)
            scored.append((cost, index, task))
        return [task for _, _, task in sorted(scored, key=lambda item: (item[0], item[1]))]

    def choose_destination(self, task: Task, candidates: Sequence[str], view: SchedulingView) -> str:
        source = view.plate_location(task)
        destination = min(candidates, key=lambda d: (self._destination_cost(task, source, d, view), d))
        self._last_cell_type[destination] = view.plate_of(task).cell_type
        self._last_assigned_at[destination] = view.now
        return destination

    def choose_robot(self, task: Task, robots: Sequence[str], view: SchedulingView) -> str:
        """Nearest idle robot to the plate (ties broken by robot ID)."""
        source = view.plate_location(task)
        return min(robots, key=lambda r: (view.travel.travel_time(view.location_of(r), source), r))

    # ------------------------------------------------------------ explanation
    def explain(self, task: Task, view: SchedulingView) -> CostBreakdown | None:
        """Terms of J for the best option available for ``task`` now, or None if it cannot start."""
        source = view.plate_location(task)
        needed = EQUIPMENT_FOR_OPERATION[task.operation]
        urgency = dict(waited_min=self._waited(task, view), critical_ratio=self._critical_ratio(task, view))

        if view.equipment[source].kind is needed:  # runs in place: no travel, no switching
            return CostBreakdown(travel_min=0.0, switch=0.0, dest_idle_min=0.0, **urgency)

        destinations = view.resources.available(needed)
        robots = view.resources.available_robots()
        if not destinations or not robots:
            return None
        robot = self.choose_robot(task, robots, view)
        best = min(destinations, key=lambda d: (self._destination_cost(task, source, d, view), d))
        return CostBreakdown(
            travel_min=view.travel.travel_time(view.location_of(robot), source)
            + view.travel.travel_time(source, best),
            switch=self._switch(task, best, view),
            dest_idle_min=self._idle(best, view),
            **urgency,
        )

    # ------------------------------------------------------------------ terms
    def _destination_cost(self, task: Task, source: str, destination: str, view: SchedulingView) -> float:
        w = self.settings.weights
        return (
            w.travel * view.travel.travel_time(source, destination)
            + w.switching * self._switch(task, destination, view)
            - w.idle * self._idle(destination, view)
        )

    def _switch(self, task: Task, destination: str, view: SchedulingView) -> float:
        if view.equipment[destination].kind not in SWITCHING_KINDS:
            return 0.0
        previous = self._last_cell_type.get(destination)
        return 1.0 if previous is not None and previous != view.plate_of(task).cell_type else 0.0

    def _idle(self, destination: str, view: SchedulingView) -> float:
        return view.now - self._last_assigned_at.get(destination, 0.0)

    @staticmethod
    def _waited(task: Task, view: SchedulingView) -> float:
        return view.now - task.ready_at if task.ready_at is not None else 0.0

    def _critical_ratio(self, task: Task, view: SchedulingView) -> float:
        """Remaining work / time left. >1 means the plate can no longer finish on time."""
        experiment = view.experiment_of(task)
        if experiment.deadline is None:
            return 0.0
        remaining = self._remaining_work(task, view)
        time_left = experiment.deadline - view.now
        if time_left <= 0:
            return self.settings.max_critical_ratio
        return min(remaining / time_left, self.settings.max_critical_ratio)

    def _remaining_work(self, task: Task, view: SchedulingView) -> float:
        steps = view.experiment_of(task).protocol.steps
        first = (task.step or 1) - 1
        return sum(step.duration_min or self.settings.default_step_min for step in steps[first:])
