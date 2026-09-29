"""Tests for anomaly detection (synthetic events and feature rows, so no full simulations)."""

import random
from typing import Any

import pytest

pytest.importorskip("sklearn")  # the anomaly layer is optional

from bioflow.analytics.anomaly_detection import (  # noqa: E402
    AnomalyModel,
    TelemetryFeatureCollector,
    evaluate,
    label_rows,
)
from bioflow.core.event_bus import EventBus  # noqa: E402
from bioflow.core.events import Event  # noqa: E402
from bioflow.domain import EquipmentKind  # noqa: E402
from bioflow.faults.fault import Fault, FaultSpec, FaultType  # noqa: E402

KINDS = {"ROBOT_01": EquipmentKind.ROBOT, "INCUBATOR_01": EquipmentKind.INCUBATOR,
         "IMAGING_01": EquipmentKind.IMAGING_STATION}


def publish(bus: EventBus, time: float, event_type: str, source: str, **payload: Any) -> None:
    bus.publish(Event(f"E{time}{event_type}", time, event_type, source, payload=payload))


# -------------------------------------------------------------- features
def test_collector_summarises_each_window() -> None:
    bus = EventBus()
    collector = TelemetryFeatureCollector(bus, KINDS, nominal_step_min=0.1, window_min=60)
    publish(bus, 10, "ROBOT_MOVED", "ROBOT_01", step_min=0.1, step_cost=1.0)
    publish(bus, 11, "ROBOT_MOVED", "ROBOT_01", step_min=0.3, step_cost=1.5)  # 2x its nominal 0.15
    publish(bus, 12, "ROBOT_WAITING", "ROBOT_01", cell=(1, 1), blocked_by="ROBOT_02")
    for t, temp in ((5, 37.2), (10, 36.9)):
        publish(bus, t, "ENVIRONMENT_READING", "INCUBATOR_01", temperature_c=temp, co2_pct=5.1,
                setpoint_temperature_c=37.0, setpoint_co2_pct=5.0)
    publish(bus, 20, "PROCESSING_STARTED", "IMAGING_01", plate_id="P", operation="IMAGE", duration_min=10)

    rows = {r["equipment_id"]: r for r in collector.rows(until=60)}

    robot = rows["ROBOT_01"]
    assert (robot["steps"], robot["waits"]) == (2, 1)
    assert robot["mean_step_ratio"] == pytest.approx(1.5) and robot["min_step_ratio"] == pytest.approx(1.0)
    incubator = rows["INCUBATOR_01"]
    assert incubator["readings"] == 2 and incubator["max_temp_dev"] == pytest.approx(0.2)
    assert incubator["mean_co2_dev"] == pytest.approx(0.1)
    assert (rows["IMAGING_01"]["runs_started"], rows["IMAGING_01"]["runs_completed"]) == (1, 0)


def test_idle_equipment_still_gets_a_row_per_window() -> None:
    collector = TelemetryFeatureCollector(EventBus(), KINDS, nominal_step_min=0.1, window_min=60)

    rows = collector.rows(until=180)

    assert len(rows) == 3 * len(KINDS)
    assert all(r["steps"] == 0 for r in rows if r["kind"] == EquipmentKind.ROBOT)


def test_windows_are_labelled_from_ground_truth() -> None:
    fault = Fault("F1", FaultSpec(FaultType.TEMPERATURE_EXCURSION, "INCUBATOR_01", 90))
    fault.injected_at, fault.repaired_at = 90.0, 150.0
    rows = [{"equipment_id": "INCUBATOR_01", "kind": EquipmentKind.INCUBATOR, "window_start": s} for s in (0, 60, 120, 180)]

    labelled = label_rows(rows, [fault], window_min=60)

    assert [r["anomalous"] for r in labelled] == [False, True, True, False]
    assert labelled[1]["fault_types"] == ["TEMPERATURE_EXCURSION"]


# ------------------------------------------------------------------ model
def robot_row(rng: random.Random, **overrides: float) -> dict[str, Any]:
    """A healthy robot window: varying activity, but the drive-time ratio is always exactly 1.0."""
    row = {"equipment_id": "ROBOT_01", "kind": EquipmentKind.ROBOT, "window_start": 0.0,
           "steps": rng.randint(0, 400), "mean_step_ratio": 1.0, "min_step_ratio": 1.0,
           "waits": rng.randint(0, 20), "pick_failures": 0, "transports": rng.randint(0, 10),
           "mean_transport_min": rng.uniform(0, 4)}
    row.update(overrides)
    return row


@pytest.fixture
def healthy() -> list[dict[str, Any]]:
    rng = random.Random(0)
    return [robot_row(rng) for _ in range(400)]


def test_forest_alone_is_blind_to_a_feature_that_never_varied(healthy: list[dict[str, Any]]) -> None:
    slow = robot_row(random.Random(9), steps=200, mean_step_ratio=1.5, min_step_ratio=1.5)

    [forest_only] = AnomalyModel(guard_degenerate=False).fit(healthy).score([slow])
    [guarded] = AnomalyModel().fit(healthy).score([slow])

    assert not forest_only["flagged"]  # the documented blind spot
    assert guarded["flagged"] and guarded["guard"] in ("mean_step_ratio", "min_step_ratio")


def test_degenerate_features_are_identified(healthy: list[dict[str, Any]]) -> None:
    model = AnomalyModel().fit(healthy)

    assert model.degenerate_features["ROBOT"] == ["mean_step_ratio", "min_step_ratio", "pick_failures"]


def test_false_alarm_rate_on_fresh_healthy_windows(healthy: list[dict[str, Any]]) -> None:
    model = AnomalyModel(false_alarm_rate=0.01).fit(healthy)
    rng = random.Random(123)

    fresh = model.score([robot_row(rng) for _ in range(500)])

    assert sum(r["flagged"] for r in fresh) / len(fresh) < 0.05


def test_evaluation_counts() -> None:
    scored = [
        {"kind": EquipmentKind.ROBOT, "anomalous": True, "flagged": True, "score": 0.9},
        {"kind": EquipmentKind.ROBOT, "anomalous": True, "flagged": False, "score": 0.6},
        {"kind": EquipmentKind.ROBOT, "anomalous": False, "flagged": True, "score": 0.7},
        {"kind": EquipmentKind.ROBOT, "anomalous": False, "flagged": False, "score": 0.1},
    ]

    [result] = evaluate(scored)

    assert (result.windows, result.anomalous, result.flagged, result.true_positives) == (4, 2, 2, 1)
    assert result.precision == 0.5 and result.recall == 0.5
    assert result.auc == pytest.approx(0.75)
