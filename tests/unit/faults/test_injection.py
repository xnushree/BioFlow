"""Tests for the fault model, fault injection (physical effects) and monitoring signals."""

import math
from typing import Any

import pytest

from bioflow.core.events import Event
from bioflow.core.exceptions import UnknownEntityError, ValidationError
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import EquipmentKind, Operation
from bioflow.equipment import EquipmentEvent, Incubator, ProcessingStation, Robot, Storage, TransportJob
from bioflow.equipment.config import ContainerSpec, IncubatorSpec, RobotSpec, StationSpec
from bioflow.faults.fault import Fault, FaultSpec, FaultStatus, FaultType, Severity
from bioflow.faults.fault_injector import FaultInjector, GroundTruthEvent, MaintenanceEvent
from bioflow.faults.monitoring import EquipmentMonitor, MonitoringEvent, MonitoringSettings
from bioflow.robotics.motion import TimedMotion
from bioflow.robotics.travel import ConstantTravelTime

TRAVEL_MIN = 10.0


class Lab:
    """A tiny hand-wired lab: storage, incubator, imager, one robot, injector, monitor."""

    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine
        self.motion = TimedMotion(engine, ConstantTravelTime(TRAVEL_MIN))
        self.storage = Storage("STORAGE_01", engine, ContainerSpec(10))
        self.incubator = Incubator("INCUBATOR_01", engine, IncubatorSpec(capacity=4))
        self.imager = ProcessingStation("IMAGING_01", engine, Operation.IMAGE, StationSpec(10))
        self.robot = Robot("ROBOT_01", engine, RobotSpec(1.0, 1.0), "STORAGE_01", self.motion)
        self.equipment: dict[str, Any] = {
            e.equipment_id: e for e in (self.storage, self.incubator, self.imager, self.robot)
        }
        self.injector = FaultInjector(engine, self.equipment, self.motion)

    def fault(self, fault_type: FaultType, equipment_id: str, at: float = 0.0,
              duration: float | None = None, **kwargs: Any) -> Fault:
        return self.injector.schedule(FaultSpec(fault_type, equipment_id, at, duration, **kwargs))

    def monitor(self, **settings: Any) -> EquipmentMonitor:
        monitor = EquipmentMonitor(
            self.engine, self.equipment, self.engine.rng, MonitoringSettings(**settings),
            other_events_pending=lambda: self.engine.pending_count > 0, work_remaining=lambda: False,
        )
        monitor.start()
        return monitor


@pytest.fixture
def lab(engine: SimulationEngine) -> Lab:
    return Lab(engine)


def of_type(log: list[Event], event_type: str) -> list[Event]:
    return [e for e in log if e.event_type == event_type]


# ------------------------------------------------------------ fault model
def test_spec_defaults_and_recoverability() -> None:
    temporary = FaultSpec(FaultType.TEMPERATURE_EXCURSION, "INCUBATOR_01", 10, 30)
    permanent = FaultSpec(FaultType.ROBOT_FAILURE, "ROBOT_01", 10)

    assert temporary.recoverable and not permanent.recoverable
    assert temporary.effect_size == 2.0  # default +2 degC
    assert FaultSpec(FaultType.TEMPERATURE_EXCURSION, "I", 0, magnitude=5).effect_size == 5
    assert temporary.severity is Severity.MEDIUM


@pytest.mark.parametrize("kwargs", [{"start_min": -1}, {"duration_min": 0}, {"equipment_id": " "}])
def test_invalid_specs(kwargs: dict[str, Any]) -> None:
    fields = {"fault_type": FaultType.ROBOT_FAILURE, "equipment_id": "ROBOT_01", "start_min": 0.0}
    with pytest.raises(ValidationError):
        FaultSpec(**{**fields, **kwargs})


def test_fault_must_target_the_right_kind_of_equipment() -> None:
    with pytest.raises(ValidationError, match="IMAGING_FAILURE cannot target ROBOT_01"):
        FaultSpec(FaultType.IMAGING_FAILURE, "ROBOT_01", 0).check_target(EquipmentKind.ROBOT)
    FaultSpec(FaultType.COMMUNICATION_TIMEOUT, "ROBOT_01", 0).check_target(EquipmentKind.ROBOT)


# -------------------------------------------------------------- injector
def test_unknown_target(lab: Lab) -> None:
    with pytest.raises(UnknownEntityError):
        lab.fault(FaultType.ROBOT_FAILURE, "ROBOT_09")


def test_overlapping_faults_of_same_type_are_rejected(lab: Lab) -> None:
    lab.fault(FaultType.COMMUNICATION_TIMEOUT, "ROBOT_01", at=10, duration=20)
    lab.fault(FaultType.COMMUNICATION_TIMEOUT, "ROBOT_01", at=30, duration=5)  # back to back is fine

    with pytest.raises(ValidationError, match="overlaps fault FLT0001"):
        lab.fault(FaultType.COMMUNICATION_TIMEOUT, "ROBOT_01", at=25)


def test_lifecycle_and_ground_truth_events(lab: Lab, event_log: list[Event]) -> None:
    fault = lab.fault(FaultType.TEMPERATURE_EXCURSION, "INCUBATOR_01", at=10, duration=30)
    assert fault.status is FaultStatus.SCHEDULED

    lab.engine.run(until=20)
    assert (fault.status, fault.injected_at, fault.is_physically_present) == (FaultStatus.ACTIVE, 10, True)

    lab.engine.run()
    assert fault.repaired_at == 40 and not fault.is_physically_present
    assert [e.event_type for e in event_log] == [
        GroundTruthEvent.FAULT_INJECTED, GroundTruthEvent.FAULT_REPAIRED, MaintenanceEvent.MAINTENANCE_COMPLETED,
    ]
    assert "fault_type" not in of_type(event_log, MaintenanceEvent.MAINTENANCE_COMPLETED)[0].payload


def test_temperature_excursion_changes_true_environment(lab: Lab) -> None:
    lab.fault(FaultType.TEMPERATURE_EXCURSION, "INCUBATOR_01", at=0, duration=30, magnitude=3.0)

    lab.engine.run(until=5)
    assert lab.incubator.true_environment(5) == (40.0, 5.0)
    lab.engine.run()
    assert lab.incubator.true_environment(40) == (37.0, 5.0)


def test_co2_excursion(lab: Lab) -> None:
    lab.fault(FaultType.CO2_EXCURSION, "INCUBATOR_01")
    lab.engine.run()

    assert lab.incubator.true_environment(0) == (37.0, 6.5)


def test_incubator_failure_drifts_towards_room_conditions(lab: Lab) -> None:
    lab.fault(FaultType.INCUBATOR_FAILURE, "INCUBATOR_01", at=100)
    lab.engine.run()

    temperature, co2 = lab.incubator.true_environment(160)  # one time constant later
    assert temperature == pytest.approx(22 + 15 * math.exp(-1))
    assert co2 == pytest.approx(0.04 + 4.96 * math.exp(-1))
    assert lab.incubator.true_environment(100) == (37.0, 5.0)


def test_healthy_sensor_reads_truth_plus_small_noise(lab: Lab) -> None:
    readings = [lab.incubator.read_sensor(lab.engine.rng) for _ in range(200)]

    assert all(abs(t - 37.0) < 0.3 and abs(c - 5.0) < 0.15 for t, c in readings)
    assert len({t for t, _ in readings}) > 150  # genuinely noisy, never exactly repeated


@pytest.mark.parametrize(("mode", "check"), [
    ("stuck", lambda first, later: later == first),
    ("dropout", lambda first, later: math.isnan(later[0]) and math.isnan(later[1])),
])
def test_sensor_failure_modes(lab: Lab, mode: str, check: Any) -> None:
    first = lab.incubator.read_sensor(lab.engine.rng)
    lab.fault(FaultType.SENSOR_FAILURE, "INCUBATOR_01", metadata={"mode": mode})
    lab.engine.run()

    later = [lab.incubator.read_sensor(lab.engine.rng) for _ in range(5)]
    assert all(check(first, reading) for reading in later)
    assert lab.incubator.true_environment(0) == (37.0, 5.0)  # the chamber itself is fine


def test_station_failure_hangs_processing(lab: Lab, make_plate, event_log: list[Event]) -> None:
    lab.imager.receive(make_plate("P1"))
    lab.imager.start_processing("P1")  # would finish at t=10
    lab.fault(FaultType.IMAGING_FAILURE, "IMAGING_01", at=5, duration=50)

    lab.engine.run()

    assert not of_type(event_log, EquipmentEvent.PROCESSING_COMPLETED)
    assert lab.imager.processing_plate_id == "P1"  # still "processing" as far as the station knows
    assert lab.imager.hardware_ok  # repaired at t=55, but the run is not resumed automatically


def test_robot_failure_freezes_and_resume_continues(lab: Lab, make_plate, event_log: list[Event]) -> None:
    lab.storage.receive(make_plate("P1"))
    lab.robot.start_transport(TransportJob("P1", lab.storage, lab.imager))  # pick 0-1, travel 1-11, place 11-12
    lab.fault(FaultType.ROBOT_FAILURE, "ROBOT_01", at=6, duration=20)

    lab.engine.run()
    assert not of_type(event_log, EquipmentEvent.PLATE_PLACED)
    assert lab.robot.carrying is not None and lab.robot.hardware_ok  # repaired at t=26 but still frozen

    lab.robot.resume()  # at t=26: 5 minutes of travel left, then the 1-minute place
    lab.engine.run()
    placed = of_type(event_log, EquipmentEvent.PLATE_PLACED)
    assert placed and placed[0].timestamp == pytest.approx(26 + 5 + 1)


def test_robot_failure_during_pick_resumes_the_pick(lab: Lab, make_plate, event_log: list[Event]) -> None:
    lab.storage.receive(make_plate("P1"))
    lab.robot.start_transport(TransportJob("P1", lab.storage, lab.imager))  # pick runs 0-1
    lab.fault(FaultType.ROBOT_FAILURE, "ROBOT_01", at=0.25, duration=10)

    lab.engine.run()
    lab.robot.resume()
    lab.engine.run()

    picked = of_type(event_log, EquipmentEvent.PLATE_PICKED)[0]
    assert picked.timestamp == pytest.approx(10.25 + 0.75)


def test_resume_before_repair_is_refused(lab: Lab) -> None:
    lab.fault(FaultType.ROBOT_FAILURE, "ROBOT_01")
    lab.engine.run()

    with pytest.raises(ValidationError, match="hardware is failed"):
        lab.robot.resume()


def test_robot_timeout_slows_travel(lab: Lab, make_plate, event_log: list[Event]) -> None:
    lab.storage.receive(make_plate("P1"))
    lab.fault(FaultType.ROBOT_TIMEOUT, "ROBOT_01", magnitude=3.0)
    lab.engine.run()

    lab.robot.start_transport(TransportJob("P1", lab.storage, lab.imager))
    lab.engine.run()

    assert of_type(event_log, EquipmentEvent.PLATE_PLACED)[0].timestamp == pytest.approx(1 + 30 + 1)


def test_plate_detection_failure_makes_picks_fail_until_repaired(
    lab: Lab, make_plate, event_log: list[Event]
) -> None:
    lab.storage.receive(make_plate("P1"))
    lab.fault(FaultType.PLATE_DETECTION_FAILURE, "ROBOT_01", at=0, duration=3.5)
    lab.engine.run(until=0)
    lab.robot.start_transport(TransportJob("P1", lab.storage, lab.imager))

    lab.engine.run()

    failures = of_type(event_log, EquipmentEvent.PICK_FAILED)
    assert [e.payload["attempt"] for e in failures] == [1, 2, 3]
    assert of_type(event_log, EquipmentEvent.PLATE_PICKED)[0].payload["plate_id"] == "P1"
    assert lab.imager.holds("P1")


# ------------------------------------------------------------- monitoring
def test_heartbeats_and_environment_readings(lab: Lab, event_log: list[Event]) -> None:
    lab.engine.schedule(10, "KEEP_ALIVE", "TEST", lambda e: None)  # something for the monitor to wait on
    lab.monitor(environment_every_n_heartbeats=5)

    lab.engine.run()

    beats = of_type(event_log, MonitoringEvent.HEARTBEAT)
    assert {e.source for e in beats} == set(lab.equipment)
    assert len([e for e in beats if e.source == "ROBOT_01"]) == 11  # t = 0..10
    readings = of_type(event_log, MonitoringEvent.ENVIRONMENT_READING)
    assert [e.timestamp for e in readings] == [4.0, 9.0]
    assert readings[0].payload["temperature_c"] == pytest.approx(37.0, abs=0.3)


def test_lost_link_silences_heartbeats(lab: Lab, event_log: list[Event]) -> None:
    lab.engine.schedule(20, "KEEP_ALIVE", "TEST", lambda e: None)
    lab.fault(FaultType.COMMUNICATION_TIMEOUT, "INCUBATOR_01", at=5, duration=10)
    lab.monitor()

    lab.engine.run()

    seen = [e.timestamp for e in of_type(event_log, MonitoringEvent.HEARTBEAT) if e.source == "INCUBATOR_01"]
    assert 4.0 in seen and 15.0 in seen
    assert not [t for t in seen if 5 <= t < 15]


def test_dead_robot_sends_no_heartbeat(lab: Lab, event_log: list[Event]) -> None:
    lab.engine.schedule(5, "KEEP_ALIVE", "TEST", lambda e: None)
    lab.fault(FaultType.ROBOT_FAILURE, "ROBOT_01", at=2)
    lab.monitor()

    lab.engine.run()

    seen = [e.timestamp for e in of_type(event_log, MonitoringEvent.HEARTBEAT) if e.source == "ROBOT_01"]
    assert seen == [0.0, 1.0]


def test_monitor_stops_when_nothing_is_left_to_watch(lab: Lab) -> None:
    monitor = lab.monitor()

    lab.engine.run()

    assert not monitor.running
    assert lab.engine.now == 0.0


def test_monitor_keeps_watching_a_frozen_lab_for_a_while(engine: SimulationEngine) -> None:
    lab = Lab(engine)
    monitor = EquipmentMonitor(engine, lab.equipment, engine.rng, MonitoringSettings(idle_ticks_before_stop=30),
                               other_events_pending=lambda: engine.pending_count > 0, work_remaining=lambda: True)
    monitor.start()

    engine.run()

    assert not monitor.running
    assert engine.now == 29.0  # 30 idle ticks, then it gives up so a stalled run can end
