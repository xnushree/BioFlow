"""The observation layer: what a real plant's supervisory system could actually see.

Every ``heartbeat_interval_min``, each piece of equipment whose link is up
sends a HEARTBEAT with its reported state. Every Nth heartbeat, incubators also
send an ENVIRONMENT_READING from their sensor (truth + noise, or garbage if
the sensor is broken). Equipment with a lost link or a dead controller sends
nothing, and that silence is itself a symptom.

The monitor keeps ticking while anything else is scheduled. When nothing else
is scheduled but work is unfinished (e.g. everything is waiting on a frozen
robot), it keeps ticking for ``idle_ticks_before_stop`` more ticks so
silence-based symptoms can still be observed, then stops so a genuinely
stalled run can end.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from bioflow.core.events import Event
from bioflow.core.simulation import SimulationContext
from bioflow.core.validation import require
from bioflow.equipment.base import Equipment
from bioflow.equipment.incubator import Incubator

SOURCE_ID = "MONITOR"


class MonitoringEvent(StrEnum):
    HEARTBEAT = "HEARTBEAT"  # source: equipment; payload: kind, state
    ENVIRONMENT_READING = "ENVIRONMENT_READING"  # source: incubator; payload: readings, setpoint, tolerance
    CYCLE_COMPLETED = "MONITOR_CYCLE"  # all heartbeats/readings for this tick have been sent


@dataclass(frozen=True)
class MonitoringSettings:
    heartbeat_interval_min: float = 1.0
    environment_every_n_heartbeats: int = 5
    idle_ticks_before_stop: int = 120

    def __post_init__(self) -> None:
        require(self.heartbeat_interval_min > 0, "heartbeat_interval_min must be positive")
        require(self.environment_every_n_heartbeats >= 1, "environment_every_n_heartbeats must be >= 1")
        require(self.idle_ticks_before_stop >= 1, "idle_ticks_before_stop must be >= 1")


class EquipmentMonitor:
    def __init__(
        self,
        context: SimulationContext,
        equipment: Mapping[str, Equipment[Any]],
        rng: random.Random,
        settings: MonitoringSettings,
        other_events_pending: Callable[[], bool],
        work_remaining: Callable[[], bool],
    ) -> None:
        self._context = context
        self._equipment = equipment
        self._rng = rng
        self.settings = settings
        self._other_events_pending = other_events_pending
        self._work_remaining = work_remaining
        self._tick_count = 0
        self._idle_ticks = 0
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Begin ticking (idempotent). Called when work arrives."""
        if not self._running:
            self._running = True
            self._idle_ticks = 0
            self._context.schedule(0.0, "MONITOR_TICK", SOURCE_ID, self._tick)

    def _tick(self, event: Event) -> None:
        self._tick_count += 1
        for equipment in self._equipment.values():
            if equipment.emits_heartbeat:
                self._context.publish(MonitoringEvent.HEARTBEAT, equipment.equipment_id,
                                      payload={"kind": equipment.kind, "state": equipment.state})
        if self._tick_count % self.settings.environment_every_n_heartbeats == 0:
            self._sample_environment()
        self._context.publish(MonitoringEvent.CYCLE_COMPLETED, SOURCE_ID, payload={"tick": self._tick_count})

        if self._other_events_pending():
            self._idle_ticks = 0
        else:
            self._idle_ticks += 1
        keep_going = self._other_events_pending() or (
            self._work_remaining() and self._idle_ticks < self.settings.idle_ticks_before_stop
        )
        if keep_going:
            self._context.schedule(self.settings.heartbeat_interval_min, "MONITOR_TICK", SOURCE_ID, self._tick)
        else:
            self._running = False

    def _sample_environment(self) -> None:
        for equipment in self._equipment.values():
            if isinstance(equipment, Incubator) and equipment.emits_heartbeat:
                temperature, co2 = equipment.read_sensor(self._rng)
                self._context.publish(MonitoringEvent.ENVIRONMENT_READING, equipment.equipment_id, payload={
                    "temperature_c": temperature, "co2_pct": co2,
                    "setpoint_temperature_c": equipment.setpoint.temperature_c,
                    "setpoint_co2_pct": equipment.setpoint.co2_pct,
                    "tolerance_temperature_c": equipment.setpoint.temperature_tolerance_c,
                    "tolerance_co2_pct": equipment.setpoint.co2_tolerance_pct,
                })
