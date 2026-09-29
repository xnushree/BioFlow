"""Tests for telemetry: event recording, metrics, and structured logging."""

import json
import logging
import math
import subprocess
import sys
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path

import pytest

from bioflow.core.clock import SimulationClock
from bioflow.core.event_bus import EventBus
from bioflow.core.events import Event
from bioflow.domain import Experiment, Operation, Protocol, ProtocolStep
from bioflow.telemetry.logger import ROOT_LOGGER_NAME, configure_logging
from bioflow.telemetry.recorder import TelemetryLevel, TelemetryRecorder, included, json_safe, read_jsonl

ROOT = Path(__file__).parents[3]
INCUBATE_720 = Protocol("inc", "HEK293", (ProtocolStep(Operation.INCUBATE, 720), ProtocolStep(Operation.ARCHIVE)))


@pytest.fixture(autouse=True)
def reset_bioflow_logger() -> Iterator[None]:
    yield
    root = logging.getLogger(ROOT_LOGGER_NAME)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


# --------------------------------------------------------------- recorder
@pytest.mark.parametrize(("event_type", "level", "expected"), [
    ("TASK_COMPLETED", TelemetryLevel.SUMMARY, True),
    ("EQUIPMENT_STATE_CHANGED", TelemetryLevel.SUMMARY, False),
    ("EQUIPMENT_STATE_CHANGED", TelemetryLevel.STANDARD, True),
    ("ROBOT_MOVED", TelemetryLevel.STANDARD, False),
    ("HEARTBEAT", TelemetryLevel.FULL, True),
])
def test_detail_levels(event_type: str, level: TelemetryLevel, expected: bool) -> None:
    assert included(event_type, level) is expected


def test_json_safe_conversion() -> None:
    class Kind(StrEnum):
        ROBOT = "ROBOT"

    value = {"kind": Kind.ROBOT, "cell": (3, 4), "temp": math.nan, "nested": {"ids": frozenset({"A"})}}

    assert json_safe(value) == {"kind": "ROBOT", "cell": [3, 4], "temp": None, "nested": {"ids": ["A"]}}


def test_recorder_writes_and_reads_jsonl(tmp_path: Path) -> None:
    bus = EventBus()
    recorder = TelemetryRecorder(bus, TelemetryLevel.STANDARD)
    bus.publish(Event("EV1", 5.0, "TASK_COMPLETED", "DISPATCHER", payload={"task_id": "T1"}))
    bus.publish(Event("EV2", 6.0, "HEARTBEAT", "ROBOT_01"))  # dropped at this level

    path = tmp_path / "run.jsonl"
    assert recorder.write_jsonl(path) == 1
    [record] = list(read_jsonl(path))
    assert (record["sim_time"], record["event_type"], record["payload"]) == (5.0, "TASK_COMPLETED", {"task_id": "T1"})
    assert isinstance(record["wall_time"], float)


def test_recorder_is_an_isolated_observer() -> None:
    bus = EventBus()
    recorder = TelemetryRecorder(bus)
    recorder._records = None  # type: ignore[assignment]  # simulate a broken recorder
    control_saw: list[str] = []
    bus.subscribe("TASK_COMPLETED", lambda e: control_saw.append(e.event_id))

    bus.publish(Event("EV1", 0.0, "TASK_COMPLETED", "DISPATCHER"))

    assert control_saw == ["EV1"]
    assert bus.stats.handler_errors == {"telemetry": 1}


# ---------------------------------------------------------------- metrics
def test_metrics_for_a_hand_checkable_run(make_lab) -> None:
    """1 plate, 1 robot, 2 min travel, 0.5 min pick/place, 720 min incubation.

    Robot busy: 0 -> 3.0 (plate to incubator) and 723.0 -> 726.0 (plate back): 6 of 726 minutes.
    INCUBATOR_01 holds the plate from 3.0 (placed) to 723.5 (picked), capacity 10.
    """
    lab = make_lab(robots={"count": 1, "pick_time_min": 0.5, "place_time_min": 0.5}, travel_min=2.0)
    lab.schedule_experiment(Experiment("EXP", INCUBATE_720, 1, 0.0))

    metrics = lab.run().metrics

    assert metrics is not None
    assert metrics.horizon_min == pytest.approx(726.0)
    assert metrics.robot_utilization["ROBOT_01"] == pytest.approx(6.0 / 726.0)
    assert metrics.incubator_utilization["INCUBATOR_01"] == pytest.approx((723.5 - 3.0) / 10 / 726.0)
    assert metrics.incubator_utilization["INCUBATOR_02"] == 0.0
    assert metrics.station_utilization == {"MEDIA_01": 0.0, "IMAGING_01": 0.0}
    assert metrics.plates_finished == 1
    assert metrics.throughput_plates_per_hour == pytest.approx(60 / 726.0)
    assert metrics.mean_task_wait_min == 0.0  # nothing ever waited for a free resource


def test_task_wait_is_measured_when_resources_are_contended(make_lab) -> None:
    image = Protocol("img", "HEK293", (ProtocolStep(Operation.IMAGE, 30), ProtocolStep(Operation.ARCHIVE)))
    lab = make_lab()
    lab.schedule_experiment(Experiment("EXP", image, 3, 0.0))  # one imager, three plates

    metrics = lab.run().metrics

    assert metrics is not None and metrics.max_task_wait_min > 30
    assert metrics.max_ready_queue >= 2


def test_metrics_are_deterministic(make_lab) -> None:
    def once():  # noqa: ANN202
        lab = make_lab()
        lab.schedule_experiment(Experiment("EXP", INCUBATE_720, 4, 0.0))
        return lab.run().metrics

    assert once() == once()


# ---------------------------------------------------------------- logging
def test_text_log_lines_carry_simulation_time(capsys: pytest.CaptureFixture[str]) -> None:
    clock = SimulationClock()
    clock.advance_to(720.0)
    configure_logging("INFO", clock=clock)

    logging.getLogger("bioflow.test").info("incubation finished")

    assert "| t=720.000 | incubation finished" in capsys.readouterr().err


def test_json_log_format(capsys: pytest.CaptureFixture[str]) -> None:
    clock = SimulationClock()
    clock.advance_to(12.5)
    configure_logging("INFO", fmt="json", clock=clock)

    logging.getLogger("bioflow.test").warning("robot timeout")

    entry = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert entry == {**entry, "level": "WARNING", "logger": "bioflow.test", "sim_time": "12.500",
                     "message": "robot timeout"}


def test_invalid_log_format_is_rejected() -> None:
    from bioflow.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="Invalid log format"):
        configure_logging("INFO", fmt="xml")


def test_cli_writes_telemetry(tmp_path: Path) -> None:
    out = tmp_path / "demo.jsonl"
    result = subprocess.run(
        [sys.executable, "simulation/run_simulation.py", "simulation/scenarios/basic_demo.yaml",
         "--telemetry", "summary", "--telemetry-out", str(out)],
        capture_output=True, text=True, cwd=ROOT, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Telemetry:" in result.stdout
    types = {record["event_type"] for record in read_jsonl(out)}
    assert "TASK_COMPLETED" in types and "ROBOT_MOVED" not in types
