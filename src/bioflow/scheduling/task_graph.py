"""Directed acyclic graph of tasks and their dependencies.

Guarantee: ``ready_tasks()`` never contains a task with an incomplete
dependency. Tasks become READY automatically when their last dependency
completes.

The graph is the only writer of task status. Changing ``task.status``
directly would bypass the READY index, so all status changes go through the
``mark_*`` methods (which are themselves checked by TASK_TRANSITIONS).

Efficiency: each PENDING task keeps a count of unfinished dependencies, and
each task keeps a set of its dependents. Completing a task therefore touches
only its direct dependents, O(out-degree), instead of rescanning the graph.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterable, Iterator

from bioflow.core.exceptions import UnknownEntityError, ValidationError
from bioflow.domain import Task, TaskStatus


class TaskGraph:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._dependents: dict[str, set[str]] = {}
        self._unfinished_deps: dict[str, int] = {}  # only for PENDING tasks
        self._ready: dict[str, Task] = {}  # insertion-ordered READY index

    # ------------------------------------------------------------------ queries
    def __len__(self) -> int:
        return len(self._tasks)

    def __contains__(self, task_id: object) -> bool:
        return task_id in self._tasks

    def __iter__(self) -> Iterator[Task]:
        return iter(self._tasks.values())

    def get(self, task_id: str) -> Task:
        try:
            return self._tasks[task_id]
        except KeyError:
            raise UnknownEntityError(task_id, "task") from None

    @property
    def ready_count(self) -> int:
        return len(self._ready)

    def ready_tasks(self) -> list[Task]:
        """READY tasks in the order they became ready."""
        return list(self._ready.values())

    def dependents(self, task_id: str) -> list[Task]:
        self.get(task_id)
        return [self._tasks[d] for d in sorted(self._dependents[task_id])]

    def tasks_for(self, experiment_id: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.experiment_id == experiment_id]

    def progress(self, experiment_id: str) -> Counter[TaskStatus]:
        return Counter(t.status for t in self.tasks_for(experiment_id))

    def is_experiment_finished(self, experiment_id: str) -> bool:
        tasks = self.tasks_for(experiment_id)
        return bool(tasks) and all(t.is_terminal for t in tasks)

    # ------------------------------------------------------------ construction
    def add_tasks(self, tasks: Iterable[Task], now: float = 0.0) -> list[Task]:
        """Add a batch of tasks atomically. Returns the tasks that are immediately READY.

        ``now`` is recorded as ``ready_at`` for tasks that are ready straight away.

        Raises:
            ValidationError: duplicate IDs, a dependency that does not exist,
                a dependency on a failed/cancelled task, or a cycle. On error the
                graph is left unchanged.
        """
        batch = list(tasks)
        self._validate_batch(batch)

        for task in batch:
            self._tasks[task.task_id] = task
            self._dependents[task.task_id] = set()
        for task in batch:
            for dep in task.depends_on:
                self._dependents[dep].add(task.task_id)

        newly_ready = []
        for task in batch:
            unfinished = sum(1 for dep in task.depends_on if self._tasks[dep].status is not TaskStatus.COMPLETED)
            if task.status is TaskStatus.PENDING and unfinished == 0:
                self._make_ready(task, now)
                newly_ready.append(task)
            elif task.status is TaskStatus.PENDING:
                self._unfinished_deps[task.task_id] = unfinished
            elif task.status is TaskStatus.READY:
                self._ready[task.task_id] = task
        return newly_ready

    def _validate_batch(self, batch: list[Task]) -> None:
        ids = [t.task_id for t in batch]
        repeated = {task_id for task_id, count in Counter(ids).items() if count > 1}
        duplicates = sorted(repeated | {task_id for task_id in ids if task_id in self._tasks})
        if duplicates:
            raise ValidationError(f"Duplicate task IDs: {duplicates}")

        batch_ids = set(ids)
        for task in batch:
            for dep in task.depends_on:
                if dep not in batch_ids and dep not in self._tasks:
                    raise ValidationError(f"{task.task_id} depends on unknown task {dep}")
                existing = self._tasks.get(dep)
                if existing is not None and existing.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
                    raise ValidationError(
                        f"{task.task_id} depends on {dep}, which is {existing.status} and will never complete"
                    )
        # Existing tasks never depend on new ones, so any cycle lies within the batch.
        cycle = _find_cycle_members(batch)
        if cycle:
            raise ValidationError(f"Dependency cycle among tasks: {cycle}")

    # --------------------------------------------------------- status changes
    def mark_running(self, task_id: str, equipment_id: str, now: float) -> None:
        task = self.get(task_id)
        task.status = TaskStatus.RUNNING  # READY -> RUNNING, checked by TASK_TRANSITIONS
        del self._ready[task_id]
        task.assigned_equipment_id = equipment_id
        task.started_at = now

    def mark_completed(self, task_id: str, now: float) -> list[Task]:
        """Complete a RUNNING task. Returns dependents that became READY as a result."""
        task = self.get(task_id)
        task.status = TaskStatus.COMPLETED
        task.completed_at = now
        newly_ready = []
        for dep_id in sorted(self._dependents[task_id]):
            if dep_id not in self._unfinished_deps:
                continue
            self._unfinished_deps[dep_id] -= 1
            if self._unfinished_deps[dep_id] == 0:
                dependent = self._tasks[dep_id]
                self._make_ready(dependent, now)
                newly_ready.append(dependent)
        return newly_ready

    def requeue(self, task_id: str, now: float) -> None:
        """Return an interrupted RUNNING task to READY (e.g. its equipment faulted)."""
        task = self.get(task_id)
        task.status = TaskStatus.READY
        task.assigned_equipment_id = None
        task.started_at = None
        task.ready_at = now
        self._ready[task_id] = task

    def mark_failed(self, task_id: str, now: float) -> list[Task]:
        """Fail a RUNNING task and cancel everything downstream of it, which can now never run.

        Returns the cancelled descendants.
        """
        task = self.get(task_id)
        task.status = TaskStatus.FAILED
        task.completed_at = now
        return self._cancel_descendants(task_id)

    def cancel(self, task_id: str) -> list[Task]:
        """Cancel a task that has not finished, and all its descendants. Returns every cancelled task."""
        task = self.get(task_id)
        self._cancel_one(task)
        return [task, *self._cancel_descendants(task_id)]

    # ------------------------------------------------------------------ helpers
    def _make_ready(self, task: Task, now: float) -> None:
        task.status = TaskStatus.READY
        task.ready_at = now
        self._unfinished_deps.pop(task.task_id, None)
        self._ready[task.task_id] = task

    def _cancel_one(self, task: Task) -> None:
        task.status = TaskStatus.CANCELLED
        self._ready.pop(task.task_id, None)
        self._unfinished_deps.pop(task.task_id, None)

    def _cancel_descendants(self, task_id: str) -> list[Task]:
        cancelled = []
        queue = deque(sorted(self._dependents[task_id]))
        while queue:
            dependent = self._tasks[queue.popleft()]
            if dependent.is_terminal:
                continue
            self._cancel_one(dependent)
            cancelled.append(dependent)
            queue.extend(sorted(self._dependents[dependent.task_id]))
        return cancelled


def _find_cycle_members(batch: list[Task]) -> list[str]:
    """Kahn's algorithm within the batch: tasks never freed are on (or behind) a cycle."""
    batch_ids = {t.task_id for t in batch}
    in_degree = {t.task_id: len(t.depends_on & batch_ids) for t in batch}
    dependents: dict[str, list[str]] = {t.task_id: [] for t in batch}
    for task in batch:
        for dep in task.depends_on & batch_ids:
            dependents[dep].append(task.task_id)

    queue = deque(tid for tid, degree in in_degree.items() if degree == 0)
    while queue:
        for dependent in dependents[queue.popleft()]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)
    return sorted(tid for tid, degree in in_degree.items() if degree > 0)
