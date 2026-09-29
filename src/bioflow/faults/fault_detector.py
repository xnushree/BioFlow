"""Infer faults from observable symptoms only.

Inputs (all observable in a real plant):
    * HEARTBEAT / MONITOR_CYCLE: who is still reporting, checked once per monitoring cycle;
    * ENVIRONMENT_READING: incubator sensor values;
    * equipment activity: movement, picks, placements, processing start/finish;
    * PICK_FAILED: robot error codes;
    * MAINTENANCE_COMPLETED: a technician signed off a repair.

Never an input: the ground-truth fault channel. A test enforces that.

Each detection belongs to a *category* per equipment, and at most one
detection per (equipment, category) is active at a time:

    link        COMMUNICATION_TIMEOUT or ROBOT_FAILURE (from silence)
    motion      ROBOT_TIMEOUT (slow drive: every recent step slower than nominal)
    transport   ROBOT_TIMEOUT (a transport far past its expected duration)
    gripper     PLATE_DETECTION_FAILURE (repeated failed picks)
    processing  MEDIA_STATION_FAILURE / IMAGING_FAILURE (overdue processing)
    environment TEMPERATURE_EXCURSION / CO2_EXCURSION / INCUBATOR_FAILURE
    sensor      SENSOR_FAILURE (implausible, missing or frozen readings)

Detections clear when their symptom goes away (heartbeats resume, readings
return to normal, a pick succeeds) or on maintenance sign-off for symptoms
that cannot resolve on their own (a hung station, a slow drive).
"""

from __future__ import annotations

import itertools
import logging
import math
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from bioflow.core.event_bus import EventBus
from bioflow.core.events import ALL_EVENTS, Event
from bioflow.core.simulation import SimulationContext
from bioflow.domain import EquipmentKind
from bioflow.equipment.events import EquipmentEvent
from bioflow.faults.config import DetectionSettings
from bioflow.faults.fault import FaultType
from bioflow.faults.fault_injector import MaintenanceEvent
from bioflow.faults.monitoring import MonitoringEvent
from bioflow.robotics.motion import MotionEvent

logger = logging.getLogger(__name__)

SOURCE_ID = "FAULT_DETECTOR"
ExpectedTransport = Callable[[str, str, str], float]  # (robot, source, destination) -> minutes

_STATION_FAULT = {
    EquipmentKind.MEDIA_STATION: FaultType.MEDIA_STATION_FAILURE,
    EquipmentKind.IMAGING_STATION: FaultType.IMAGING_FAILURE,
}
# Equipment-originated events that prove the equipment is physically active.
_ACTIVITY_EVENTS = frozenset({
    MotionEvent.ROBOT_MOVED, EquipmentEvent.PLATE_PICKED, EquipmentEvent.PLATE_PLACED, EquipmentEvent.PICK_FAILED,
    EquipmentEvent.PLATE_RECEIVED, EquipmentEvent.PLATE_RELEASED, EquipmentEvent.PROCESSING_COMPLETED,
})


class DetectionEvent(StrEnum):
    FAULT_DETECTED = "FAULT_DETECTED"  # payload: detection_id, fault_type, equipment_id, category, evidence
    FAULT_CLEARED = "FAULT_CLEARED"  # payload: detection_id, fault_type, equipment_id, category, reason


@dataclass(eq=False)
class Detection:
    detection_id: str
    fault_type: FaultType
    equipment_id: str
    category: str
    detected_at: float
    evidence: str
    cleared_at: float | None = None

    @property
    def active(self) -> bool:
        return self.cleared_at is None


@dataclass
class _Timer:
    started_at: float
    expected_min: float
    plate_id: str  # which job this times; see _on_transport_completed


class FaultDetector:
    def __init__(
        self,
        context: SimulationContext,
        bus: EventBus,
        equipment_kinds: Mapping[str, EquipmentKind],
        settings: DetectionSettings,
        expected_transport: ExpectedTransport,
        heartbeat_interval_min: float,
        nominal_step_min: float | None = None,
    ) -> None:
        """``nominal_step_min``: time for a robot to cross one ordinary cell (map-based motion only)."""
        self._context = context
        self._kinds = dict(equipment_kinds)
        self.settings = settings
        self._expected_transport = expected_transport
        self._heartbeat_interval = heartbeat_interval_min
        self._nominal_step = nominal_step_min
        self._recent_moves: dict[str, deque[float]] = {}  # robot -> recent step drive-time ratios
        self._blocked_by: dict[str, str] = {}  # robot -> robot it is currently queued behind
        self._last_moved: dict[str, float] = {}
        self._detections: list[Detection] = []
        self._active: dict[tuple[str, str], Detection] = {}
        self._ids = itertools.count(1)

        now = context.now
        self._last_heartbeat = {eid: now for eid in self._kinds}
        self._last_activity = {eid: -math.inf for eid in self._kinds}
        self._transports: dict[str, _Timer] = {}
        self._processing: dict[str, _Timer] = {}
        self._readings: dict[str, deque[tuple[float, float]]] = {}
        self._out_streak: dict[str, dict[str, int]] = {}
        self._normal_streak: dict[str, int] = {}

        bus.subscribe(MonitoringEvent.HEARTBEAT, self._on_heartbeat)
        bus.subscribe(MonitoringEvent.CYCLE_COMPLETED, self._on_cycle)
        bus.subscribe(MonitoringEvent.ENVIRONMENT_READING, self._on_reading)
        bus.subscribe(EquipmentEvent.TRANSPORT_STARTED, self._on_transport_started)
        bus.subscribe(EquipmentEvent.TRANSPORT_COMPLETED, self._on_transport_completed)
        bus.subscribe(EquipmentEvent.TRANSPORT_ABORTED, self._on_transport_completed)
        bus.subscribe(EquipmentEvent.PROCESSING_STARTED, self._on_processing_started)
        bus.subscribe(EquipmentEvent.PROCESSING_COMPLETED, self._on_processing_completed)
        bus.subscribe(EquipmentEvent.PICK_FAILED, self._on_pick_failed)
        bus.subscribe(EquipmentEvent.PLATE_PICKED, self._on_plate_picked)
        bus.subscribe(MaintenanceEvent.MAINTENANCE_COMPLETED, self._on_maintenance)
        bus.subscribe(MotionEvent.ROBOT_MOVED, self._on_robot_moved)
        bus.subscribe(MotionEvent.ROBOT_WAITING, self._on_robot_waiting)
        bus.subscribe(ALL_EVENTS, self._on_any_event)

    # ------------------------------------------------------------------ queries
    @property
    def detections(self) -> list[Detection]:
        return list(self._detections)

    def active_detections(self, equipment_id: str | None = None) -> list[Detection]:
        return [d for d in self._active.values() if equipment_id is None or d.equipment_id == equipment_id]

    # ---------------------------------------------------------- liveness (link)
    def _on_any_event(self, event: Event) -> None:
        if event.source in self._kinds and event.event_type in _ACTIVITY_EVENTS:
            self._last_activity[event.source] = event.timestamp

    def _on_heartbeat(self, event: Event) -> None:
        self._last_heartbeat[event.source] = event.timestamp
        if (event.source, "link") in self._active:
            self._clear(event.source, "link", "heartbeats resumed")
            # The silent period is already explained by the link detection; time the job afresh.
            timer = self._transports.get(event.source)
            if timer is not None:
                timer.started_at = event.timestamp

    def _on_cycle(self, event: Event) -> None:
        now = self._context.now
        s = self.settings
        for eid, last in self._last_heartbeat.items():
            silence = now - last
            if silence > s.heartbeat_timeout_min and (eid, "link") not in self._active:
                self._detect(eid, "link", *self._classify_silence(eid, last, silence))
        for robot, timer in self._transports.items():
            if self._faulted_blocker(robot) is not None:
                timer.started_at = now  # time stuck behind a diagnosed fault is explained: don't count it
                continue
            limit = timer.expected_min * s.transport_timeout_factor + s.transport_timeout_margin_min
            if now - timer.started_at > limit and (robot, "link") not in self._active and self._stalled(robot, now):
                self._detect(robot, "transport", FaultType.ROBOT_TIMEOUT,
                             f"transport running {now - timer.started_at:.1f} min, expected ~{timer.expected_min:.1f}")
        for station, timer in list(self._processing.items()):
            limit = timer.expected_min * s.processing_timeout_factor + s.processing_timeout_margin_min
            if now - timer.started_at > limit:
                # The run is considered lost: stop timing it; maintenance sign-off clears the station.
                del self._processing[station]
                self._detect(station, "processing", _STATION_FAULT[self._kinds[station]],
                             f"processing running {now - timer.started_at:.1f} min, expected {timer.expected_min:.1f}")

    def _classify_silence(self, eid: str, last_heartbeat: float, silence: float) -> tuple[FaultType, str]:
        """No heartbeat. Still physically active -> the link is down; frozen mid-job -> the robot is down.

        Only activity *after the first missed heartbeat* counts: a robot that moved and then died
        between two heartbeats must not look alive.
        """
        if self._last_activity[eid] > last_heartbeat + self._heartbeat_interval:
            return FaultType.COMMUNICATION_TIMEOUT, f"no heartbeat for {silence:.1f} min but still active"
        if self._kinds[eid] is EquipmentKind.ROBOT and eid in self._transports:
            return FaultType.ROBOT_FAILURE, f"no heartbeat for {silence:.1f} min and no progress on its job"
        return FaultType.COMMUNICATION_TIMEOUT, f"no heartbeat for {silence:.1f} min"

    # -------------------------------------------------------- jobs and timing
    def _on_transport_started(self, event: Event) -> None:
        p = event.payload
        self._transports[event.source] = _Timer(
            event.timestamp, self._expected_transport(event.source, p["source"], p["destination"]), p["plate_id"]
        )

    def _on_transport_completed(self, event: Event) -> None:
        # Match on the plate, not just the robot, so one job's completion can never cancel the timer
        # of the robot's next job. (This originally guarded against nested event delivery; the bus
        # now delivers in causal order, and the check remains as cheap defence in depth.)
        self._stop_timer(self._transports, event.source, event.payload["plate_id"])
        self._clear(event.source, "transport", "transport finished")

    def _on_processing_started(self, event: Event) -> None:
        if self._kinds.get(event.source) in _STATION_FAULT:
            self._processing[event.source] = _Timer(
                event.timestamp, float(event.payload["duration_min"]), event.payload["plate_id"]
            )

    def _on_processing_completed(self, event: Event) -> None:
        self._stop_timer(self._processing, event.source, event.payload["plate_id"])

    @staticmethod
    def _stop_timer(timers: dict[str, _Timer], equipment_id: str, plate_id: str) -> None:
        timer = timers.get(equipment_id)
        if timer is not None and timer.plate_id == plate_id:
            del timers[equipment_id]

    def forget_processing(self, station_id: str) -> None:
        """Recovery aborted the hung run; stop timing it."""
        self._processing.pop(station_id, None)

    def forget_transport(self, robot_id: str) -> None:
        self._transports.pop(robot_id, None)

    def _stalled(self, robot: str, now: float) -> bool:
        """With position tracking, an overdue robot only counts as stuck if it has not moved for a while
        (a robot creeping through heavy traffic is progressing; a slow drive has its own rule)."""
        if self._nominal_step is None:
            return True  # no position tracking: elapsed time is all there is
        return now - self._last_moved.get(robot, -math.inf) > self.settings.transport_stall_min

    def _on_robot_waiting(self, event: Event) -> None:
        self._blocked_by[event.source] = event.payload["blocked_by"]

    def _faulted_blocker(self, robot: str) -> str | None:
        """The robot (directly or further up a queue) with an active diagnosis that ``robot`` is stuck behind.

        A delay explained by a known fault elsewhere is a *consequential* symptom, not a new fault:
        alarming on it would take a healthy robot out of service.
        """
        seen = {robot}
        blocker = self._blocked_by.get(robot)
        while blocker is not None and blocker not in seen:
            if self.active_detections(blocker):
                return blocker
            seen.add(blocker)
            blocker = self._blocked_by.get(blocker)
        return None

    def _on_robot_moved(self, event: Event) -> None:
        self._blocked_by.pop(event.source, None)  # moving again: no longer stuck behind anyone
        self._last_moved[event.source] = event.timestamp
        if self._nominal_step is None:
            return
        s = self.settings
        # Drive time of the step relative to its nominal time; waiting before a step is excluded,
        # so following a slow robot or queueing in traffic does not look like a slow drive.
        ratio = float(event.payload["step_min"]) / (self._nominal_step * float(event.payload["step_cost"]))
        ratios = self._recent_moves.setdefault(event.source, deque(maxlen=s.slow_step_window))
        ratios.append(ratio)
        if len(ratios) == s.slow_step_window and min(ratios) > s.slow_step_factor:
            self._detect(event.source, "motion", FaultType.ROBOT_TIMEOUT,
                         f"last {s.slow_step_window} steps each took at least {min(ratios):.1f}x nominal drive time")

    def _on_pick_failed(self, event: Event) -> None:
        attempts = int(event.payload["attempt"])
        if attempts >= self.settings.pick_failure_threshold:
            self._detect(event.source, "gripper", FaultType.PLATE_DETECTION_FAILURE,
                         f"{attempts} consecutive pick attempts reported 'plate not detected'")

    def _on_plate_picked(self, event: Event) -> None:
        self._clear(event.source, "gripper", "pick succeeded")

    def _on_maintenance(self, event: Event) -> None:
        self._recent_moves.pop(event.source, None)  # judge a repaired drive afresh
        for category in ("processing", "motion", "gripper"):
            self._clear(event.source, category, "maintenance completed")

    # ------------------------------------------------------------ environment
    def _on_reading(self, event: Event) -> None:
        eid, p, s = event.source, event.payload, self.settings
        temperature, co2 = float(p["temperature_c"]), float(p["co2_pct"])
        history = self._readings.setdefault(eid, deque(maxlen=s.stuck_sensor_repeats))
        history.append((temperature, co2))

        sensor_problem = self._sensor_problem(temperature, co2, history)
        if sensor_problem:
            self._detect(eid, "sensor", FaultType.SENSOR_FAILURE, sensor_problem)
            return  # the reading cannot be trusted, so it says nothing about the chamber
        self._clear(eid, "sensor", "plausible, changing readings again")

        out = {
            "temperature": abs(temperature - p["setpoint_temperature_c"]) > p["tolerance_temperature_c"],
            "co2": abs(co2 - p["setpoint_co2_pct"]) > p["tolerance_co2_pct"],
        }
        streak = self._out_streak.setdefault(eid, {"temperature": 0, "co2": 0})
        for key, is_out in out.items():
            streak[key] = streak[key] + 1 if is_out else 0
        self._normal_streak[eid] = 0 if any(out.values()) else self._normal_streak.get(eid, 0) + 1

        if (eid, "environment") in self._active:
            if self._normal_streak[eid] >= s.normal_confirmations:
                self._clear(eid, "environment", "readings back within tolerance")
            return
        confirmed = {key for key, count in streak.items() if count >= s.excursion_confirmations}
        evidence = f"temperature {temperature:.2f} degC, CO2 {co2:.2f} %"
        if confirmed and out["temperature"] and out["co2"]:
            self._detect(eid, "environment", FaultType.INCUBATOR_FAILURE,
                         f"temperature and CO2 both out of tolerance ({evidence}): climate control lost")
        elif "temperature" in confirmed:
            self._detect(eid, "environment", FaultType.TEMPERATURE_EXCURSION, evidence)
        elif "co2" in confirmed:
            self._detect(eid, "environment", FaultType.CO2_EXCURSION, evidence)

    def _sensor_problem(self, temperature: float, co2: float, history: deque[tuple[float, float]]) -> str | None:
        s = self.settings
        if math.isnan(temperature) or math.isnan(co2):
            return "sensor returned no value"
        t_low, t_high = s.plausible_temperature_c
        c_low, c_high = s.plausible_co2_pct
        if not (t_low <= temperature <= t_high and c_low <= co2 <= c_high):
            return f"physically implausible reading ({temperature:.2f} degC, {co2:.2f} %)"
        if len(history) == s.stuck_sensor_repeats and len(set(history)) == 1:
            return f"{s.stuck_sensor_repeats} identical readings: sensor frozen"
        return None

    # ---------------------------------------------------------------- output
    def _detect(self, eid: str, category: str, fault_type: FaultType, evidence: str) -> None:
        if (eid, category) in self._active:
            return
        detection = Detection(f"DET{next(self._ids):04d}", fault_type, eid, category, self._context.now, evidence)
        self._detections.append(detection)
        self._active[(eid, category)] = detection
        logger.warning("detected %s on %s: %s", fault_type, eid, evidence)
        self._context.publish(DetectionEvent.FAULT_DETECTED, SOURCE_ID, target=eid, payload={
            "detection_id": detection.detection_id, "fault_type": fault_type, "equipment_id": eid,
            "category": category, "evidence": evidence,
        })

    def _clear(self, eid: str, category: str, reason: str) -> None:
        detection = self._active.pop((eid, category), None)
        if detection is None:
            return
        detection.cleared_at = self._context.now
        self._context.publish(DetectionEvent.FAULT_CLEARED, SOURCE_ID, target=eid, payload={
            "detection_id": detection.detection_id, "fault_type": detection.fault_type, "equipment_id": eid,
            "category": category, "reason": reason,
        })
