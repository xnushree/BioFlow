"""Structured event recording (a non-critical bus observer).

Each record is a flat, JSON-safe dict:
    sim_time, wall_time, event_id, event_type, source, target, payload

Detail levels keep volume under control. A 1,000-plate run produces hundreds of
thousands of robot steps and heartbeats, which ``full`` keeps and the others drop.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Iterable, Mapping
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

from bioflow.core.event_bus import EventBus
from bioflow.core.events import ALL_EVENTS, Event


class TelemetryLevel(StrEnum):
    SUMMARY = "summary"  # experiment/task lifecycle, faults, recovery
    STANDARD = "standard"  # + equipment state changes, reservations, transports, processing
    FULL = "full"  # + heartbeats, sensor readings, every robot step


_SUMMARY_TYPES = frozenset({
    "EXPERIMENT_SUBMITTED", "EXPERIMENT_FINISHED", "TASK_DISPATCHED", "TASK_COMPLETED", "TASK_REQUEUED",
    "FAULT_DETECTED", "FAULT_CLEARED", "RECOVERY_STARTED", "RECOVERY_BLOCKED", "RECOVERY_COMPLETED",
    "UNRECOVERABLE_FAULT", "DEADLOCK_DETECTED", "DEADLOCK_RESOLVED", "MAINTENANCE_COMPLETED",
    "GROUND_TRUTH_FAULT_INJECTED", "GROUND_TRUTH_FAULT_REPAIRED",
})
_FULL_ONLY_TYPES = frozenset({"HEARTBEAT", "MONITOR_CYCLE", "ENVIRONMENT_READING", "ROBOT_MOVED", "ROBOT_WAITING"})


def included(event_type: str, level: TelemetryLevel) -> bool:
    if level is TelemetryLevel.FULL:
        return True
    if level is TelemetryLevel.SUMMARY:
        return event_type in _SUMMARY_TYPES
    return event_type not in _FULL_ONLY_TYPES


def json_safe(value: Any) -> Any:
    """Convert payload values (enums, tuples, NaN, nested mappings) into plain JSON types."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    return value


class TelemetryRecorder:
    def __init__(self, bus: EventBus, level: TelemetryLevel = TelemetryLevel.STANDARD) -> None:
        self.level = level
        self._records: list[dict[str, Any]] = []
        bus.subscribe(ALL_EVENTS, self._record, critical=False, name="telemetry")

    @property
    def records(self) -> list[dict[str, Any]]:
        return list(self._records)

    def _record(self, event: Event) -> None:
        if not included(event.event_type, self.level):
            return
        self._records.append({
            "sim_time": event.timestamp,
            "wall_time": time.time(),
            "event_id": event.event_id,
            "event_type": str(event.event_type),
            "source": event.source,
            "target": event.target,
            "payload": json_safe(event.payload),
        })

    def of_type(self, *event_types: str) -> list[dict[str, Any]]:
        wanted = set(event_types)
        return [r for r in self._records if r["event_type"] in wanted]

    def write_jsonl(self, path: Path) -> int:
        """Write one JSON object per line. Returns the number of records written."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in self._records:
                handle.write(json.dumps(record) + "\n")
        return len(self._records)


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)
