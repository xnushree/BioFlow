"""Tests for fault detection rules, fed with synthetic observations at controlled times."""

import math
from typing import Any

import pytest

from bioflow.core.exceptions import ConfigurationError
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import EquipmentKind
from bioflow.equipment.events import EquipmentEvent
from bioflow.faults.config import DetectionSettings, parse_fault_config
from bioflow.faults.diagnostics import evaluate_detection
from bioflow.faults.fault import Fault, FaultSpec, FaultType
from bioflow.faults.fault_detector import Detection, FaultDetector
from bioflow.faults.fault_injector import GroundTruthEvent, MaintenanceEvent
from bioflow.faults.monitoring import MonitoringEvent
from bioflow.robotics.motion import MotionEvent

KINDS = {
    "ROBOT_01": EquipmentKind.ROBOT,
    "INCUBATOR_01": EquipmentKind.INCUBATOR,
    "IMAGING_01": EquipmentKind.IMAGING_STATION,
}
NOMINAL_STEP = 0.1


class Feed:
    """Drives a FaultDetector by publishing observations at chosen simulation times."""

    def __init__(self, engine: SimulationEngine, **settings: Any) -> None:
        self.engine = engine
        self.detector = FaultDetector(
            engine, engine.bus, KINDS, DetectionSettings(**settings),
            expected_transport=lambda robot, source, destination: 2.0,
            heartbeat_interval_min=1.0, nominal_step_min=NOMINAL_STEP,
        )

    def at(self, time: float, event_type: str, origin: str, /, **payload: Any) -> None:
        self.engine.schedule_at(time, "FEED", "TEST",
                                lambda e: self.engine.publish(event_type, origin, payload=payload))

    def heartbeats(self, until: float, silent: dict[str, float] | None = None) -> None:
        """Every equipment beats each minute, except ``silent[eid]`` onwards; a monitor cycle follows."""
        silent = silent or {}
        for minute in range(int(until) + 1):
            for eid in KINDS:
                if minute < silent.get(eid, math.inf):
                    self.at(minute, MonitoringEvent.HEARTBEAT, eid)
            self.at(minute + 0.001, MonitoringEvent.CYCLE_COMPLETED, "MONITOR")

    def reading(self, time: float, temperature: float, co2: float) -> None:
        self.at(time, MonitoringEvent.ENVIRONMENT_READING, "INCUBATOR_01", temperature_c=temperature, co2_pct=co2,
                setpoint_temperature_c=37.0, setpoint_co2_pct=5.0,
                tolerance_temperature_c=0.5, tolerance_co2_pct=0.5)

    def run(self) -> list[Detection]:
        self.engine.run()
        return self.detector.detections


def types(detections: list[Detection]) -> list[tuple[str, str]]:
    return [(d.fault_type, d.equipment_id) for d in detections]


# ------------------------------------------------------------------ link
def test_silent_station_is_a_communication_fault_that_clears(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    feed.heartbeats(until=10, silent={"IMAGING_01": 3})
    feed.at(8.5, MonitoringEvent.HEARTBEAT, "IMAGING_01")

    [detection] = feed.run()

    assert (detection.fault_type, detection.equipment_id) == ("COMMUNICATION_TIMEOUT", "IMAGING_01")
    assert detection.detected_at == pytest.approx(5.001)  # last beat t=2; silence exceeds 3 min at the t=5 cycle
    assert detection.cleared_at == 8.5


def test_silent_robot_frozen_mid_job_is_a_robot_failure(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    feed.at(0.5, EquipmentEvent.TRANSPORT_STARTED, "ROBOT_01", plate_id="P1", source="A", destination="B")
    feed.at(1.5, MotionEvent.ROBOT_MOVED, "ROBOT_01")  # moved, then died between heartbeats
    feed.heartbeats(until=8, silent={"ROBOT_01": 2})

    assert types(feed.run()) == [("ROBOT_FAILURE", "ROBOT_01")]


def test_silent_robot_that_keeps_moving_has_lost_its_link(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    feed.at(0.5, EquipmentEvent.TRANSPORT_STARTED, "ROBOT_01", plate_id="P1", source="A", destination="B")
    for t in (3.5, 4.5, 5.5):
        feed.at(t, MotionEvent.ROBOT_MOVED, "ROBOT_01")
    feed.heartbeats(until=8, silent={"ROBOT_01": 2})

    assert types(feed.run()) == [("COMMUNICATION_TIMEOUT", "ROBOT_01")]


def test_next_job_started_during_previous_completion_is_still_tracked(engine: SimulationEngine) -> None:
    """Regression: nested delivery makes START(new job) arrive before COMPLETE(old job)."""
    feed = Feed(engine)
    feed.at(1.2, EquipmentEvent.TRANSPORT_STARTED, "ROBOT_01", plate_id="P2", source="A", destination="B")
    feed.at(1.2, EquipmentEvent.TRANSPORT_COMPLETED, "ROBOT_01", plate_id="P1", source="C", destination="A")
    feed.heartbeats(until=8, silent={"ROBOT_01": 2})

    assert types(feed.run()) == [("ROBOT_FAILURE", "ROBOT_01")]


# ---------------------------------------------------------------- motion
def test_overdue_transport_is_a_robot_timeout(engine: SimulationEngine) -> None:
    feed = Feed(engine, transport_timeout_factor=3.0, transport_timeout_margin_min=4.0)  # limit 3*2+4 = 10
    feed.at(0.5, EquipmentEvent.TRANSPORT_STARTED, "ROBOT_01", plate_id="P1", source="A", destination="B")
    feed.heartbeats(until=12)

    [detection] = feed.run()
    assert detection.fault_type == "ROBOT_TIMEOUT" and detection.detected_at == pytest.approx(11.001)


def test_consistently_slow_steps_are_a_robot_timeout(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    for n in range(6):
        feed.at(1 + n * 0.25, MotionEvent.ROBOT_MOVED, "ROBOT_01")  # 2.5x the nominal 0.1 min per cell

    assert types(feed.run()) == [("ROBOT_TIMEOUT", "ROBOT_01")]


def test_waiting_in_traffic_is_not_a_slow_drive(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    for t in (1.0, 1.1, 3.0, 3.1, 6.0, 6.1, 9.0):  # long waits, but normal-speed steps in between
        feed.at(t, MotionEvent.ROBOT_MOVED, "ROBOT_01")

    assert feed.run() == []


# ------------------------------------------------------------ processing
def test_hung_processing_detected_once_and_cleared_by_maintenance(engine: SimulationEngine) -> None:
    feed = Feed(engine)  # limit 1.5 * 10 + 5 = 20 min
    feed.at(1, EquipmentEvent.PROCESSING_STARTED, "IMAGING_01", plate_id="P1", duration_min=10)
    feed.heartbeats(until=40)
    feed.at(35, MaintenanceEvent.MAINTENANCE_COMPLETED, "IMAGING_01", equipment_id="IMAGING_01")

    [detection] = feed.run()

    assert detection.fault_type == "IMAGING_FAILURE"
    assert detection.detected_at == pytest.approx(21.001)  # started t=1, limit 20 min
    assert detection.cleared_at == 35


def test_processing_that_finishes_is_not_a_fault(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    feed.at(1, EquipmentEvent.PROCESSING_STARTED, "IMAGING_01", plate_id="P1", duration_min=10)
    feed.at(11, EquipmentEvent.PROCESSING_COMPLETED, "IMAGING_01", plate_id="P1")
    feed.heartbeats(until=40)

    assert feed.run() == []


# ----------------------------------------------------------- environment
def test_one_bad_reading_is_not_enough(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    feed.reading(5, 39.0, 5.0)
    feed.reading(10, 37.0, 5.0)

    assert feed.run() == []


def test_confirmed_temperature_excursion_then_recovery(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    for t, temp in ((5, 38.2), (10, 38.3), (15, 37.1), (20, 36.9)):
        feed.reading(t, temp, 5.01)

    [detection] = feed.run()

    assert (detection.fault_type, detection.detected_at, detection.cleared_at) == ("TEMPERATURE_EXCURSION", 10, 20)


def test_co2_excursion(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    feed.reading(5, 37.0, 6.2)
    feed.reading(10, 37.02, 6.3)

    assert types(feed.run()) == [("CO2_EXCURSION", "INCUBATOR_01")]


def test_temperature_and_co2_together_mean_climate_control_failure(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    feed.reading(5, 35.0, 4.7)
    feed.reading(10, 33.0, 4.3)

    assert types(feed.run()) == [("INCUBATOR_FAILURE", "INCUBATOR_01")]


@pytest.mark.parametrize(("readings", "evidence"), [
    ([(math.nan, math.nan)], "no value"),
    ([(95.0, 5.0)], "implausible"),
    ([(37.02, 5.01)] * 3, "identical readings"),
])
def test_sensor_failures(engine: SimulationEngine, readings: list[tuple[float, float]], evidence: str) -> None:
    feed = Feed(engine)
    for n, (t, c) in enumerate(readings):
        feed.reading(5 * (n + 1), t, c)

    [detection] = feed.run()
    assert detection.fault_type == "SENSOR_FAILURE" and evidence in detection.evidence


def test_broken_sensor_readings_are_not_used_for_environment(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    feed.reading(5, 95.0, 5.0)
    feed.reading(10, 96.0, 5.0)  # "out of tolerance" twice, but implausible: not an excursion

    assert types(feed.run()) == [("SENSOR_FAILURE", "INCUBATOR_01")]


# ---------------------------------------------------------------- gripper
def test_repeated_pick_failures_then_success(engine: SimulationEngine) -> None:
    feed = Feed(engine)
    for attempt in (1, 2, 3):
        feed.at(attempt, EquipmentEvent.PICK_FAILED, "ROBOT_01", plate_id="P1", source="A", attempt=attempt)
    feed.at(4, EquipmentEvent.PLATE_PICKED, "ROBOT_01", plate_id="P1", source="A")

    [detection] = feed.run()
    assert (detection.fault_type, detection.detected_at, detection.cleared_at) == ("PLATE_DETECTION_FAILURE", 3, 4)


# ------------------------------------------------------- integrity checks
def test_detector_never_listens_to_ground_truth(make_lab) -> None:
    lab = make_lab()

    for event_type in GroundTruthEvent:
        assert lab.engine.bus.subscriber_count(event_type) == 0


def test_diagnostics_scoring() -> None:
    def fault(fid: str, ftype: FaultType, eid: str, start: float, end: float | None) -> Fault:
        f = Fault(fid, FaultSpec(ftype, eid, start))
        f.injected_at, f.repaired_at = start, end
        return f

    faults = [
        fault("F1", FaultType.TEMPERATURE_EXCURSION, "INCUBATOR_01", 100, 160),
        fault("F2", FaultType.ROBOT_FAILURE, "ROBOT_01", 200, 250),
        fault("F3", FaultType.IMAGING_FAILURE, "IMAGING_01", 300, 310),
    ]
    detections = [
        Detection("D1", FaultType.TEMPERATURE_EXCURSION, "INCUBATOR_01", "environment", 109, ""),
        Detection("D2", FaultType.COMMUNICATION_TIMEOUT, "ROBOT_01", "link", 203, ""),
        Detection("D3", FaultType.CO2_EXCURSION, "INCUBATOR_01", "environment", 500, ""),
    ]

    report = evaluate_detection(faults, detections)

    assert (report.correct, report.misclassified, report.missed, report.false_positives) == (1, 1, ("F3",), ("D3",))
    assert report.mean_latency_min == pytest.approx((9 + 3) / 2)
    assert "diagnosed as COMMUNICATION_TIMEOUT" in report.format()


@pytest.mark.parametrize(("data", "message"), [
    ({"detection": {"heartbeat_timout_min": 3}}, "did you mean 'heartbeat_timeout_min'"),
    ({"detection": {"slow_step_factor": 1.0}}, "slow_step_factor must be > 1"),
    ({"detection": {"plausible_co2_pct": [5, 1]}}, "low < high"),
    ({"monitoring": {"heartbeat_interval_min": "often"}}, "expected a number"),
    ({"alerts": {}}, "unknown key 'alerts'"),
])
def test_invalid_fault_config(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        parse_fault_config(data)
