"""End-of-run summary computed from the final laboratory state.

Everything is derived from task timestamps and statuses already stored in the
twin; nothing is tracked separately. Richer metrics (utilisation, queue
lengths, delay distributions) come with telemetry in Phase 19 and
benchmarking in Phase 24.
"""

from __future__ import annotations

from dataclasses import dataclass

from bioflow.control.state_manager import StateManager
from bioflow.core.clock import format_sim_time
from bioflow.domain import ExperimentStatus, TaskStatus
from bioflow.faults.diagnostics import DetectionReport
from bioflow.faults.recovery import RecoveryReport
from bioflow.robotics.motion import MotionStats


@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: str
    protocol: str
    plates: int
    priority: int
    status: ExperimentStatus
    finished_at: float | None
    deadline: float | None

    @property
    def late(self) -> bool:
        return self.deadline is not None and self.finished_at is not None and self.finished_at > self.deadline


@dataclass(frozen=True)
class RunSummary:
    scheduler: str
    end_time: float
    makespan: float
    tasks_total: int
    tasks_completed: int
    events_processed: int
    experiments: tuple[ExperimentResult, ...]
    stalled_tasks: tuple[str, ...]  # unfinished tasks when the simulation ran out of events
    motion: MotionStats | None = None  # robot coordination statistics (map-based runs only)
    faults: DetectionReport | None = None  # detection scorecard (runs with injected faults only)
    recovery: RecoveryReport | None = None  # what automatic recovery did (runs with detections only)

    @property
    def stalled(self) -> bool:
        return bool(self.stalled_tasks)

    @property
    def deadline_violations(self) -> int:
        return sum(result.late for result in self.experiments)

    def format(self) -> str:
        lines = [
            f"Scheduler:          {self.scheduler}",
            f"Makespan:           {self.makespan:.1f} min ({format_sim_time(self.makespan)})",
            f"Tasks completed:    {self.tasks_completed}/{self.tasks_total}",
            f"Deadline misses:    {self.deadline_violations}",
            f"Events processed:   {self.events_processed}",
            *([f"Robot movement:     {self.motion.steps} steps, {self.motion.waits} waits, "
               f"{self.motion.reroutes} reroutes, {self.motion.deadlocks} deadlocks resolved"]
              if self.motion else []),
            "",
            f"{'Experiment':<12}{'Protocol':<20}{'Plates':>7}{'Prio':>6}  {'Status':<10}{'Finished':>10}"
            f"{'Deadline':>10}  Late",
        ]
        for r in self.experiments:
            finished = f"{r.finished_at:.1f}" if r.finished_at is not None else "-"
            deadline = f"{r.deadline:.1f}" if r.deadline is not None else "-"
            lines.append(
                f"{r.experiment_id:<12}{r.protocol:<20}{r.plates:>7}{r.priority:>6}  {r.status:<10}"
                f"{finished:>10}{deadline:>10}  {'YES' if r.late else ''}"
            )
        if self.faults:
            lines += ["", "Fault detection (scored against ground truth):", self.faults.format()]
        if self.recovery:
            lines += ["", self.recovery.format()]
        if self.stalled:
            shown = ", ".join(self.stalled_tasks[:10])
            more = f" (+{len(self.stalled_tasks) - 10} more)" if len(self.stalled_tasks) > 10 else ""
            lines += ["", f"STALLED: {len(self.stalled_tasks)} task(s) can never run: {shown}{more}"]
        return "\n".join(lines)


def summarize(
    state: StateManager,
    scheduler: str,
    end_time: float,
    events_processed: int,
    queue_empty: bool,
    motion: MotionStats | None = None,
    faults: DetectionReport | None = None,
    recovery: RecoveryReport | None = None,
) -> RunSummary:
    tasks = list(state.tasks)
    completion_times = [t.completed_at for t in tasks if t.completed_at is not None]
    results = []
    for experiment in state.experiments.values():
        exp_tasks = state.tasks.tasks_for(experiment.experiment_id)
        finished = state.tasks.is_experiment_finished(experiment.experiment_id)
        results.append(ExperimentResult(
            experiment_id=experiment.experiment_id,
            protocol=experiment.protocol.name,
            plates=experiment.plate_count,
            priority=experiment.priority,
            status=experiment.status,
            finished_at=max(t.completed_at or 0.0 for t in exp_tasks) if finished else None,
            deadline=experiment.deadline,
        ))
    unfinished = tuple(t.task_id for t in tasks if not t.is_terminal)
    return RunSummary(
        scheduler=scheduler,
        end_time=end_time,
        makespan=max(completion_times, default=0.0),
        tasks_total=len(tasks),
        tasks_completed=sum(t.status is TaskStatus.COMPLETED for t in tasks),
        events_processed=events_processed,
        experiments=tuple(results),
        stalled_tasks=unfinished if queue_empty else (),
        motion=motion,
        faults=faults,
        recovery=recovery,
    )
