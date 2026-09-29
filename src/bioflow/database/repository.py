"""The data-access layer: the only place in BioFlow-X that contains SQL.

Writes take domain/runtime objects (a finished Laboratory and its RunSummary);
reads return plain dicts (JSON-ready, which suits the REST API).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from bioflow.analytics.summary import RunSummary
from bioflow.core.exceptions import UnknownEntityError
from bioflow.database.database import Database
from bioflow.laboratory import Laboratory
from bioflow.telemetry.recorder import json_safe

Row = dict[str, Any]

_TABLES = ("experiments", "plates", "equipment", "tasks", "events", "faults", "detections", "recoveries")


class RunRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    # ------------------------------------------------------------------ writes
    def save_run(
        self,
        lab: Laboratory,
        summary: RunSummary,
        label: str,
        scenario: str | None = None,
        seed: int | None = None,
    ) -> int:
        """Persist one finished run in a single transaction. Returns its ``run_id``."""
        metrics = summary.metrics
        with self._db.transaction() as db:
            run_id = db.execute(
                """INSERT INTO runs (label, scenario, seed, scheduler, created_at, end_time, makespan,
                       tasks_total, tasks_completed, deadline_misses, events_processed, stalled,
                       metrics_json, summary_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (label, scenario, seed, summary.scheduler, datetime.now(UTC).isoformat(), summary.end_time,
                 summary.makespan, summary.tasks_total, summary.tasks_completed, summary.deadline_violations,
                 summary.events_processed, int(summary.stalled),
                 json.dumps(metrics.as_dict()) if metrics else None, summary.format()),
            ).lastrowid
            assert run_id is not None
            self._insert(db, "experiments", run_id, [
                (r.experiment_id, r.protocol, r.plates, r.priority,
                 lab.state.experiment(r.experiment_id).submitted_at, r.deadline, str(r.status), r.finished_at,
                 int(r.late))
                for r in summary.experiments
            ])
            self._insert(db, "plates", run_id, [
                (p.plate_id, p.experiment_id, p.cell_type, str(p.state), p.location_id, str(p.contamination))
                for p in lab.state.plates.values()
            ])
            utilization = {
                **(metrics.robot_utilization if metrics else {}), **(metrics.station_utilization if metrics else {}),
                **(metrics.incubator_utilization if metrics else {}),
            }
            self._insert(db, "equipment", run_id, [
                (eid, str(eq.kind), str(eq.state), utilization.get(eid), json.dumps(json_safe(eq.snapshot())))
                for eid, eq in lab.state.equipment.items()
            ])
            self._insert(db, "tasks", run_id, [
                (t.task_id, t.experiment_id, t.plate_id, str(t.operation), t.step, str(t.status),
                 t.assigned_equipment_id, t.duration_min, t.ready_at, t.started_at, t.completed_at,
                 json.dumps(sorted(t.depends_on)))
                for t in lab.state.tasks
            ])
            if lab.recorder is not None:
                self._insert(db, "events", run_id, [
                    (r["event_id"], r["sim_time"], r["event_type"], r["source"], r["target"], json.dumps(r["payload"]))
                    for r in lab.recorder.records
                ])
            self._insert(db, "faults", run_id, [
                (f.fault_id, str(f.fault_type), f.equipment_id, str(f.spec.severity), f.spec.start_min,
                 f.spec.duration_min, f.injected_at, f.repaired_at)
                for f in lab.injector.faults
            ])
            self._insert(db, "detections", run_id, [
                (d.detection_id, str(d.fault_type), d.equipment_id, d.category, d.detected_at, d.cleared_at,
                 d.evidence)
                for d in lab.detector.detections
            ])
            self._insert(db, "recoveries", run_id, [
                (r.recovery_id, r.detection_id, str(r.fault_type), r.equipment_id, r.started_at, r.completed_at,
                 int(r.unrecoverable), r.blocked_reason, r.rescheduled_tasks, json.dumps(r.affected_plates),
                 json.dumps(r.actions))
                for r in lab.recovery.recoveries
            ])
        return int(run_id)

    def delete_run(self, run_id: int) -> None:
        self.run(run_id)  # raises if unknown
        with self._db.transaction() as db:
            db.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    # ------------------------------------------------------------------- reads
    def runs(self) -> list[Row]:
        return self._query(
            """SELECT run_id, label, scenario, seed, scheduler, created_at, makespan, tasks_total,
                      tasks_completed, deadline_misses, stalled
               FROM runs ORDER BY run_id"""
        )

    def run(self, run_id: int) -> Row:
        rows = self._query("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        if not rows:
            raise UnknownEntityError(str(run_id), "run")
        row = rows[0]
        row["metrics"] = json.loads(row.pop("metrics_json")) if row["metrics_json"] else None
        return row

    def experiments(self, run_id: int) -> list[Row]:
        return self._query("SELECT * FROM experiments WHERE run_id = ? ORDER BY experiment_id", (run_id,))

    def plates(self, run_id: int, experiment_id: str | None = None) -> list[Row]:
        sql, params = "SELECT * FROM plates WHERE run_id = ?", [run_id]
        if experiment_id is not None:
            sql, params = sql + " AND experiment_id = ?", [*params, experiment_id]
        return self._query(sql + " ORDER BY plate_id", params)

    def equipment(self, run_id: int) -> list[Row]:
        rows = self._query("SELECT * FROM equipment WHERE run_id = ? ORDER BY equipment_id", (run_id,))
        for row in rows:
            row["snapshot"] = json.loads(row.pop("snapshot_json"))
        return rows

    def tasks(self, run_id: int, status: str | None = None, plate_id: str | None = None) -> list[Row]:
        """The executed schedule: each task with the equipment that ran it and its timing."""
        sql, params = "SELECT * FROM tasks WHERE run_id = ?", [run_id]
        if status is not None:
            sql, params = sql + " AND status = ?", [*params, status]
        if plate_id is not None:
            sql, params = sql + " AND plate_id = ?", [*params, plate_id]
        rows = self._query(sql + " ORDER BY started_at IS NULL, started_at, task_id", params)
        for row in rows:
            row["depends_on"] = json.loads(row.pop("depends_on_json"))
        return rows

    def events(self, run_id: int, event_type: str | None = None, source: str | None = None,
               limit: int | None = None) -> list[Row]:
        sql, params = "SELECT * FROM events WHERE run_id = ?", [run_id]
        if event_type is not None:
            sql, params = sql + " AND event_type = ?", [*params, event_type]
        if source is not None:
            sql, params = sql + " AND source = ?", [*params, source]
        sql += " ORDER BY sim_time, event_id"
        if limit is not None:
            sql, params = sql + " LIMIT ?", [*params, limit]
        rows = self._query(sql, params)
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
        return rows

    def faults(self, run_id: int) -> list[Row]:
        return self._query("SELECT * FROM faults WHERE run_id = ? ORDER BY start_min", (run_id,))

    def detections(self, run_id: int) -> list[Row]:
        return self._query("SELECT * FROM detections WHERE run_id = ? ORDER BY detected_at", (run_id,))

    def recoveries(self, run_id: int) -> list[Row]:
        rows = self._query("SELECT * FROM recoveries WHERE run_id = ? ORDER BY started_at", (run_id,))
        for row in rows:
            row["affected_plates"] = json.loads(row.pop("affected_plates_json"))
            row["actions"] = json.loads(row.pop("actions_json"))
        return rows

    def compare_runs(self, run_ids: Sequence[int]) -> list[Row]:
        """Headline numbers and metrics side by side, e.g. the same scenario under different schedulers."""
        return [
            {key: run[key] for key in ("run_id", "label", "scheduler", "makespan", "tasks_completed",
                                       "tasks_total", "deadline_misses")} | (run["metrics"] or {})
            for run in (self.run(run_id) for run_id in run_ids)
        ]

    # ------------------------------------------------------------------ helpers
    def _insert(self, db: Any, table: str, run_id: int, rows: Iterable[tuple[Any, ...]]) -> None:
        assert table in _TABLES  # table names cannot be parameters; only known names are allowed
        rows = [(run_id, *row) for row in rows]
        if rows:
            placeholders = ", ".join("?" * len(rows[0]))
            db.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[Row]:
        return [dict(row) for row in self._db.connection.execute(sql, params).fetchall()]
