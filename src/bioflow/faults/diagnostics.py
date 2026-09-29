"""Score fault detection against ground truth (for analysis only, after or outside the run).

For every injected fault, the first detection on the same equipment made
while the fault was physically present (or within a grace period after
repair) counts as its match. A match with the same fault type is *correct*,
otherwise *misclassified*. Injected faults with no match are *missed*.
Detections that match no injected fault are *false positives*.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from bioflow.faults.fault import Fault
from bioflow.faults.fault_detector import Detection


@dataclass(frozen=True)
class DetectionMatch:
    fault_id: str
    injected_type: str
    detected_type: str
    equipment_id: str
    latency_min: float

    @property
    def correct(self) -> bool:
        return self.injected_type == self.detected_type


@dataclass(frozen=True)
class DetectionReport:
    matches: tuple[DetectionMatch, ...]
    missed: tuple[str, ...]  # fault IDs never detected
    false_positives: tuple[str, ...]  # detection IDs with no injected fault behind them

    @property
    def correct(self) -> int:
        return sum(m.correct for m in self.matches)

    @property
    def misclassified(self) -> int:
        return len(self.matches) - self.correct

    @property
    def mean_latency_min(self) -> float | None:
        return mean(m.latency_min for m in self.matches) if self.matches else None

    def format(self) -> str:
        injected = len(self.matches) + len(self.missed)
        latency = f"{self.mean_latency_min:.1f} min" if self.mean_latency_min is not None else "-"
        lines = [
            f"Faults injected:    {injected}",
            f"Detected:           {len(self.matches)} ({self.correct} correctly classified, "
            f"{self.misclassified} misclassified), mean latency {latency}",
            f"Missed:             {len(self.missed)}",
            f"False positives:    {len(self.false_positives)}",
        ]
        for m in self.matches:
            verdict = "ok" if m.correct else f"diagnosed as {m.detected_type}"
            lines.append(f"  {m.fault_id} {m.injected_type:<24} {m.equipment_id:<13} "
                         f"+{m.latency_min:5.1f} min  {verdict}")
        return "\n".join(lines)


def evaluate_detection(
    faults: list[Fault], detections: list[Detection], grace_min: float = 10.0
) -> DetectionReport:
    matches: list[DetectionMatch] = []
    missed: list[str] = []
    used: set[str] = set()
    for fault in sorted((f for f in faults if f.injected_at is not None), key=lambda f: f.injected_at or 0.0):
        assert fault.injected_at is not None
        window_end = (fault.repaired_at + grace_min) if fault.repaired_at is not None else float("inf")
        candidates = [
            d for d in detections
            if d.detection_id not in used and d.equipment_id == fault.equipment_id
            and fault.injected_at <= d.detected_at <= window_end
        ]
        if not candidates:
            missed.append(fault.fault_id)
            continue
        # Prefer a detection of the right type; otherwise the earliest one.
        best = next((d for d in candidates if d.fault_type == fault.fault_type), candidates[0])
        used.add(best.detection_id)
        matches.append(DetectionMatch(
            fault.fault_id, fault.fault_type, best.fault_type, fault.equipment_id,
            best.detected_at - fault.injected_at,
        ))
    false_positives = tuple(d.detection_id for d in detections if d.detection_id not in used)
    return DetectionReport(tuple(matches), tuple(missed), false_positives)
