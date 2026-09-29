"""Time-weighted operational metrics, collected as a non-critical bus observer.

    robot utilization      fraction of time a robot is doing a job (ASSIGNED..PLACING)
    station utilization    fraction of time a media/imaging station is PROCESSING
    incubator utilization  average occupied fraction of capacity (occupancy x time)
    ready queue            READY-task count, sampled every monitoring cycle
    task wait              time from READY to started, per task
    throughput             finished plates per hour of makespan

All figures are measured over the horizon [0, makespan], so the monitoring
tail after the last task does not dilute utilization. They are deterministic
for a given seed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from statistics import mean
from typing import Any

from bioflow.control.state_manager import StateManager
from bioflow.core.event_bus import EventBus
from bioflow.core.events import Event
from bioflow.domain import EquipmentKind
from bioflow.equipment.base import Equipment
from bioflow.equipment.container import ContainerEquipment
from bioflow.equipment.events import EquipmentEvent
from bioflow.faults.monitoring import MonitoringEvent

MINUTES_PER_HOUR = 60.0
_ROBOT_BUSY = frozenset({"ASSIGNED", "MOVING", "PICKING", "TRANSPORTING", "PLACING"})
_STATION_BUSY = frozenset({"PROCESSING"})
_STATIONS = frozenset({EquipmentKind.MEDIA_STATION, EquipmentKind.IMAGING_STATION})
_HOLDERS = frozenset({EquipmentKind.INCUBATOR})


@dataclass(frozen=True)
class RunMetrics:
    horizon_min: float
    robot_utilization: dict[str, float]
    station_utilization: dict[str, float]
    incubator_utilization: dict[str, float]
    mean_ready_queue: float
    max_ready_queue: int
    mean_task_wait_min: float
    max_task_wait_min: float
    plates_finished: int
    throughput_plates_per_hour: float
    tasks_rescheduled: int

    @staticmethod
    def _avg(values: dict[str, float]) -> float:
        return mean(values.values()) if values else 0.0

    @property
    def mean_robot_utilization(self) -> float:
        return self._avg(self.robot_utilization)

    @property
    def mean_station_utilization(self) -> float:
        return self._avg(self.station_utilization)

    @property
    def mean_incubator_utilization(self) -> float:
        return self._avg(self.incubator_utilization)

    def format(self) -> str:
        return "\n".join([
            f"Utilization:        robots {self.mean_robot_utilization:.0%}, stations "
            f"{self.mean_station_utilization:.0%}, incubators {self.mean_incubator_utilization:.0%}",
            f"Ready queue:        mean {self.mean_ready_queue:.1f}, max {self.max_ready_queue}",
            f"Task wait:          mean {self.mean_task_wait_min:.1f} min, max {self.max_task_wait_min:.1f} min",
            f"Throughput:         {self.throughput_plates_per_hour:.2f} plates/hour "
            f"({self.plates_finished} plates), {self.tasks_rescheduled} tasks rescheduled",
        ])

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon_min": self.horizon_min,
            "mean_robot_utilization": self.mean_robot_utilization,
            "mean_station_utilization": self.mean_station_utilization,
            "mean_incubator_utilization": self.mean_incubator_utilization,
            "mean_ready_queue": self.mean_ready_queue,
            "max_ready_queue": self.max_ready_queue,
            "mean_task_wait_min": self.mean_task_wait_min,
            "max_task_wait_min": self.max_task_wait_min,
            "plates_finished": self.plates_finished,
            "throughput_plates_per_hour": self.throughput_plates_per_hour,
            "tasks_rescheduled": self.tasks_rescheduled,
        }


class _Accumulator:
    """A piecewise-constant signal over time (starting at 0), integrated up to any horizon."""

    def __init__(self) -> None:
        self.changes: list[tuple[float, float]] = [(0.0, 0.0)]  # (time, new value)

    def set(self, now: float, value: float) -> None:
        self.changes.append((now, value))

    def integral_until(self, horizon: float) -> float:
        area = 0.0
        for (t0, v), (t1, _) in zip(self.changes, [*self.changes[1:], (float("inf"), 0.0)], strict=False):
            if t0 >= horizon:
                break
            area += v * (min(t1, horizon) - t0)
        return area


class MetricsCollector:
    def __init__(
        self,
        bus: EventBus,
        equipment: Mapping[str, Equipment[Any]],
        ready_count: Callable[[], int],
    ) -> None:
        self._equipment = equipment
        self._ready_count = ready_count
        self._busy: dict[str, _Accumulator] = {}
        self._occupancy: dict[str, _Accumulator] = {}
        for eid, eq in equipment.items():
            if eq.kind is EquipmentKind.ROBOT or eq.kind in _STATIONS:
                self._busy[eid] = _Accumulator()
            elif eq.kind in _HOLDERS:
                self._occupancy[eid] = _Accumulator()
        self._queue_samples: list[tuple[float, int]] = []
        self._rescheduled = 0
        bus.subscribe(EquipmentEvent.STATE_CHANGED, self._on_state_changed, critical=False, name="metrics")
        bus.subscribe(EquipmentEvent.PLATE_RECEIVED, self._on_occupancy, critical=False, name="metrics")
        bus.subscribe(EquipmentEvent.PLATE_RELEASED, self._on_occupancy, critical=False, name="metrics")
        bus.subscribe(MonitoringEvent.CYCLE_COMPLETED, self._on_cycle, critical=False, name="metrics")
        bus.subscribe("TASK_REQUEUED", self._on_requeued, critical=False, name="metrics")

    def _on_state_changed(self, event: Event) -> None:
        accumulator = self._busy.get(event.source)
        if accumulator is None:
            return
        busy_states = _ROBOT_BUSY if event.payload["kind"] == EquipmentKind.ROBOT else _STATION_BUSY
        accumulator.set(event.timestamp, 1.0 if str(event.payload["to_state"]) in busy_states else 0.0)

    def _on_occupancy(self, event: Event) -> None:
        accumulator = self._occupancy.get(event.source)
        if accumulator is not None:
            container = self._equipment[event.source]
            assert isinstance(container, ContainerEquipment)
            accumulator.set(event.timestamp, container.occupancy / container.capacity)

    def _on_cycle(self, event: Event) -> None:
        self._queue_samples.append((event.timestamp, self._ready_count()))

    @property
    def queue_samples(self) -> list[tuple[float, int]]:
        """(simulation time, READY-task count) at each monitoring cycle."""
        return list(self._queue_samples)

    def _on_requeued(self, event: Event) -> None:
        self._rescheduled += 1

    def finalize(self, state: StateManager, horizon_min: float) -> RunMetrics:
        horizon = max(horizon_min, 1e-9)
        in_horizon = [count for t, count in self._queue_samples if t <= horizon_min] or [0]
        waits = [t.started_at - t.ready_at for t in state.tasks
                 if t.started_at is not None and t.ready_at is not None and t.started_at >= t.ready_at]
        finished = sum(p.is_finished for p in state.plates.values())

        def utilisation(accumulators: dict[str, _Accumulator], kinds: frozenset[EquipmentKind]) -> dict[str, float]:
            return {eid: acc.integral_until(horizon_min) / horizon for eid, acc in accumulators.items()
                    if self._equipment[eid].kind in kinds}

        return RunMetrics(
            horizon_min=horizon_min,
            robot_utilization=utilisation(self._busy, frozenset({EquipmentKind.ROBOT})),
            station_utilization=utilisation(self._busy, _STATIONS),
            incubator_utilization=utilisation(self._occupancy, _HOLDERS),
            mean_ready_queue=mean(in_horizon),
            max_ready_queue=max(in_horizon),
            mean_task_wait_min=mean(waits) if waits else 0.0,
            max_task_wait_min=max(waits, default=0.0),
            plates_finished=finished,
            throughput_plates_per_hour=finished / (horizon / MINUTES_PER_HOUR) if horizon_min > 0 else 0.0,
            tasks_rescheduled=self._rescheduled,
        )
