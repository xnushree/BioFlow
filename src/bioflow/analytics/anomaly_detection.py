"""Anomaly detection on simulated equipment telemetry (optional, machine learning).

This complements the rule-based detector; it does not replace it. The rules
have deliberate thresholds (for example, a slow drive is only diagnosed above
2x nominal, a temperature excursion only beyond +-0.5 degC), so gradual or
subtle degradation below those thresholds is invisible to them. An anomaly
model trained only on *healthy* operation can flag windows that look unusual
without needing a threshold per symptom.

Pipeline:
    1. TelemetryFeatureCollector (a non-critical bus observer) summarises each
       piece of equipment per time window into a feature vector.
    2. AnomalyModel fits one Isolation Forest per equipment kind on healthy runs,
       with its alarm threshold set at a chosen quantile of healthy scores
       (i.e. a target false-alarm rate on healthy windows). An Isolation Forest
       isolates points by random splits *within the training range* of each
       feature, so it is blind to a feature that never varied in healthy data
       (e.g. a robot's drive-time ratio, always exactly 1.0 in the simulation).
       Such degenerate features get an extrapolation guard: a clear departure
       from their healthy constant is anomalous in itself.
    3. evaluate() scores labelled windows from faulted runs against ground truth.

Everything here is about the simulated lab; nothing claims to predict failures
of real equipment.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from bioflow.core.event_bus import EventBus
from bioflow.core.events import Event
from bioflow.domain import EquipmentKind
from bioflow.equipment.events import EquipmentEvent
from bioflow.faults.fault import Fault
from bioflow.faults.monitoring import MonitoringEvent
from bioflow.robotics.motion import MotionEvent

FEATURES: dict[EquipmentKind, tuple[str, ...]] = {
    EquipmentKind.ROBOT: ("steps", "mean_step_ratio", "min_step_ratio", "waits", "pick_failures",
                          "transports", "mean_transport_min"),
    EquipmentKind.INCUBATOR: ("readings", "mean_temp_dev", "max_temp_dev", "temp_spread", "mean_co2_dev",
                              "max_co2_dev"),
    EquipmentKind.MEDIA_STATION: ("runs_started", "runs_completed"),
    EquipmentKind.IMAGING_STATION: ("runs_started", "runs_completed"),
}


@dataclass
class _Window:
    step_ratios: list[float]
    waits: int = 0
    pick_failures: int = 0
    transports: list[float] | None = None
    temp_devs: list[float] | None = None
    co2_devs: list[float] | None = None
    runs_started: int = 0
    runs_completed: int = 0


class TelemetryFeatureCollector:
    """Summarise observable telemetry per (equipment, time window). Uses only what monitoring can see."""

    def __init__(self, bus: EventBus, kinds: Mapping[str, EquipmentKind], nominal_step_min: float | None,
                 window_min: float = 60.0) -> None:
        self._kinds = {eid: kind for eid, kind in kinds.items() if kind in FEATURES}
        self._nominal = nominal_step_min
        self.window_min = window_min
        self._windows: dict[tuple[str, int], _Window] = defaultdict(lambda: _Window([], transports=[], temp_devs=[],
                                                                                 co2_devs=[]))
        self._transport_start: dict[str, float] = {}
        self._last_time = 0.0
        subscriptions = {
            MotionEvent.ROBOT_MOVED: self._on_moved, MotionEvent.ROBOT_WAITING: self._on_waiting,
            EquipmentEvent.PICK_FAILED: self._on_pick_failed, EquipmentEvent.TRANSPORT_STARTED: self._on_started,
            EquipmentEvent.TRANSPORT_COMPLETED: self._on_completed,
            EquipmentEvent.PROCESSING_STARTED: self._on_processing_started,
            EquipmentEvent.PROCESSING_COMPLETED: self._on_processing_completed,
            MonitoringEvent.ENVIRONMENT_READING: self._on_reading,
        }
        for event_type, handler in subscriptions.items():
            bus.subscribe(event_type, handler, critical=False, name="anomaly-features")

    def _window(self, event: Event) -> _Window:
        self._last_time = max(self._last_time, event.timestamp)
        return self._windows[(event.source, int(event.timestamp // self.window_min))]

    def _on_moved(self, event: Event) -> None:
        if self._nominal and event.source in self._kinds:
            ratio = float(event.payload["step_min"]) / (self._nominal * float(event.payload["step_cost"]))
            self._window(event).step_ratios.append(ratio)

    def _on_waiting(self, event: Event) -> None:
        if event.source in self._kinds:
            self._window(event).waits += 1

    def _on_pick_failed(self, event: Event) -> None:
        self._window(event).pick_failures += 1

    def _on_started(self, event: Event) -> None:
        self._transport_start[event.source] = event.timestamp

    def _on_completed(self, event: Event) -> None:
        started = self._transport_start.pop(event.source, None)
        if started is not None:
            window = self._window(event)
            assert window.transports is not None
            window.transports.append(event.timestamp - started)

    def _on_processing_started(self, event: Event) -> None:
        if self._kinds.get(event.source) in (EquipmentKind.MEDIA_STATION, EquipmentKind.IMAGING_STATION):
            self._window(event).runs_started += 1

    def _on_processing_completed(self, event: Event) -> None:
        if self._kinds.get(event.source) in (EquipmentKind.MEDIA_STATION, EquipmentKind.IMAGING_STATION):
            self._window(event).runs_completed += 1

    def _on_reading(self, event: Event) -> None:
        p = event.payload
        temperature, co2 = float(p["temperature_c"]), float(p["co2_pct"])
        if math.isnan(temperature) or math.isnan(co2):
            return  # a broken sensor is the rule-based detector's job
        window = self._window(event)
        assert window.temp_devs is not None and window.co2_devs is not None
        window.temp_devs.append(temperature - float(p["setpoint_temperature_c"]))
        window.co2_devs.append(co2 - float(p["setpoint_co2_pct"]))

    def rows(self, until: float | None = None) -> list[dict[str, Any]]:
        """One feature row per equipment per complete window (idle equipment included)."""
        horizon = until if until is not None else self._last_time
        last_window = int(horizon // self.window_min)
        out = []
        for eid, kind in sorted(self._kinds.items()):
            for index in range(last_window):
                window = self._windows.get((eid, index)) or _Window([], transports=[], temp_devs=[], co2_devs=[])
                out.append({"equipment_id": eid, "kind": kind, "window_start": index * self.window_min,
                            **_features(kind, window)})
        return out


def _features(kind: EquipmentKind, w: _Window) -> dict[str, float]:
    if kind is EquipmentKind.ROBOT:
        return {"steps": len(w.step_ratios), "mean_step_ratio": mean(w.step_ratios) if w.step_ratios else 1.0,
                "min_step_ratio": min(w.step_ratios) if w.step_ratios else 1.0, "waits": w.waits,
                "pick_failures": w.pick_failures, "transports": len(w.transports or []),
                "mean_transport_min": mean(w.transports) if w.transports else 0.0}
    if kind is EquipmentKind.INCUBATOR:
        temps, co2s = w.temp_devs or [], w.co2_devs or []
        return {"readings": len(temps), "mean_temp_dev": mean(temps) if temps else 0.0,
                "max_temp_dev": max((abs(t) for t in temps), default=0.0),
                "temp_spread": pstdev(temps) if len(temps) > 1 else 0.0,
                "mean_co2_dev": mean(co2s) if co2s else 0.0, "max_co2_dev": max((abs(c) for c in co2s), default=0.0)}
    return {"runs_started": w.runs_started, "runs_completed": w.runs_completed}


def label_rows(rows: Sequence[dict[str, Any]], faults: Sequence[Fault], window_min: float) -> list[dict[str, Any]]:
    """Mark each window anomalous if an injected fault was physically present on that equipment during it."""
    labelled = []
    for row in rows:
        start, end = row["window_start"], row["window_start"] + window_min
        hits = [f for f in faults if f.equipment_id == row["equipment_id"] and f.injected_at is not None
                and f.injected_at < end and (f.repaired_at is None or f.repaired_at > start)]
        labelled.append({**row, "anomalous": bool(hits), "fault_types": sorted({str(f.fault_type) for f in hits})})
    return labelled


DEGENERATE_RANGE = 1e-6  # a feature whose healthy range is below this (relative) never really varied
DEGENERATE_TOLERANCE = 0.01  # relative departure from the healthy constant that counts as anomalous


class AnomalyModel:
    """One Isolation Forest per equipment kind, trained on healthy windows only (plus degenerate-feature guards)."""

    def __init__(self, false_alarm_rate: float = 0.01, seed: int = 0, guard_degenerate: bool = True) -> None:
        self.false_alarm_rate = false_alarm_rate
        self.seed = seed
        self.guard_degenerate = guard_degenerate
        self._models: dict[EquipmentKind, tuple[StandardScaler, IsolationForest, float]] = {}
        self._constants: dict[EquipmentKind, dict[str, float]] = {}

    def fit(self, healthy_rows: Sequence[dict[str, Any]]) -> AnomalyModel:
        for kind, names in FEATURES.items():
            matrix = _matrix([r for r in healthy_rows if r["kind"] == kind], names)
            if len(matrix) < 10:
                continue
            scaler = StandardScaler().fit(matrix)
            forest = IsolationForest(n_estimators=200, random_state=self.seed).fit(scaler.transform(matrix))
            scores = -forest.score_samples(scaler.transform(matrix))  # higher = more anomalous
            threshold = float(np.quantile(scores, 1.0 - self.false_alarm_rate))
            self._models[kind] = (scaler, forest, threshold)
            low, high = matrix.min(axis=0), matrix.max(axis=0)
            self._constants[kind] = {
                name: float(low[i]) for i, name in enumerate(names)
                if high[i] - low[i] <= DEGENERATE_RANGE * max(1.0, abs(float(high[i])))
            }
        return self

    @property
    def degenerate_features(self) -> dict[str, list[str]]:
        return {str(kind): sorted(values) for kind, values in self._constants.items()}

    def _departure(self, row: dict[str, Any]) -> str | None:
        """Name of a degenerate feature that clearly left its healthy constant, if any."""
        if not self.guard_degenerate:
            return None
        for name, constant in self._constants.get(row["kind"], {}).items():
            if abs(float(row[name]) - constant) > DEGENERATE_TOLERANCE * max(1.0, abs(constant)):
                return name
        return None

    def score(self, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        scored = []
        for row in rows:
            model = self._models.get(row["kind"])
            if model is None:
                continue
            scaler, forest, threshold = model
            value = float(-forest.score_samples(scaler.transform(_matrix([row], FEATURES[row["kind"]])))[0])
            departure = self._departure(row)
            if departure is not None:
                value = max(value, threshold) + 1.0  # rank guard hits above every forest-only score
            scored.append({**row, "score": value, "flagged": value > threshold, "guard": departure})
        return scored


def _matrix(rows: Sequence[dict[str, Any]], names: Sequence[str]) -> np.ndarray:
    return np.array([[float(row[name]) for name in names] for row in rows], dtype=float)


@dataclass(frozen=True)
class KindResult:
    kind: str
    windows: int
    anomalous: int
    flagged: int
    true_positives: int
    auc: float | None

    @property
    def precision(self) -> float | None:
        return self.true_positives / self.flagged if self.flagged else None

    @property
    def recall(self) -> float | None:
        return self.true_positives / self.anomalous if self.anomalous else None


def evaluate(scored: Sequence[dict[str, Any]]) -> list[KindResult]:
    results = []
    for kind in FEATURES:
        rows = [r for r in scored if r["kind"] == kind]
        if not rows:
            continue
        labels = [r["anomalous"] for r in rows]
        auc = roc_auc_score(labels, [r["score"] for r in rows]) if 0 < sum(labels) < len(labels) else None
        results.append(KindResult(str(kind), len(rows), sum(labels), sum(r["flagged"] for r in rows),
                                  sum(r["flagged"] and r["anomalous"] for r in rows), auc))
    return results
