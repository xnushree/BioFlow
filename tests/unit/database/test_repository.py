"""Tests for SQLite persistence through the repository."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bioflow.core.exceptions import ConfigurationError, UnknownEntityError
from bioflow.database import Database, RunRepository
from bioflow.database.__main__ import main
from bioflow.domain import Experiment, Operation, Protocol, ProtocolStep
from bioflow.faults.fault import FaultSpec, FaultType
from bioflow.telemetry.recorder import TelemetryLevel

PROTOCOL = Protocol("p", "HEK293", (
    ProtocolStep(Operation.INCUBATE, 120), ProtocolStep(Operation.IMAGE), ProtocolStep(Operation.ARCHIVE),
))


@pytest.fixture
def repo() -> Iterator[RunRepository]:
    database = Database()  # in memory
    yield RunRepository(database)
    database.close()


@pytest.fixture
def save_run(make_lab, repo: RunRepository):  # noqa: ANN201
    """Run a small lab (optionally with faults) and save it; returns (run_id, lab, summary)."""

    def run(plates: int = 2, faults: tuple[FaultSpec, ...] = (), label: str = "test", **lab_options: Any):
        lab = make_lab(telemetry=TelemetryLevel.SUMMARY, **lab_options)
        lab.schedule_experiment(Experiment("EXP", PROTOCOL, plates, 0.0, priority=2, deadline=500))
        for fault in faults:
            lab.schedule_fault(fault)
        summary = lab.run()
        return repo.save_run(lab, summary, label=label, scenario="unit", seed=0), lab, summary

    return run


def test_run_round_trip(repo: RunRepository, save_run) -> None:
    run_id, lab, summary = save_run()

    run = repo.run(run_id)
    assert (run["label"], run["scheduler"], run["tasks_completed"]) == ("test", "fifo", summary.tasks_completed)
    assert run["makespan"] == summary.makespan
    assert run["metrics"]["plates_finished"] == 2
    assert "Tasks completed:" in run["summary_text"]


def test_experiments_plates_and_equipment(repo: RunRepository, save_run) -> None:
    run_id, lab, _ = save_run()

    [experiment] = repo.experiments(run_id)
    assert (experiment["experiment_id"], experiment["priority"], experiment["status"]) == ("EXP", 2, "COMPLETED")
    plates = repo.plates(run_id)
    assert [p["plate_id"] for p in plates] == ["EXP-P001", "EXP-P002"]
    assert {p["state"] for p in plates} == {"ARCHIVED"}
    equipment = {e["equipment_id"]: e for e in repo.equipment(run_id)}
    assert set(equipment) == set(lab.state.equipment)
    assert equipment["IMAGING_01"]["utilization"] > 0
    assert equipment["IMAGING_01"]["snapshot"]["kind"] == "IMAGING_STATION"


def test_tasks_are_the_executed_schedule(repo: RunRepository, save_run) -> None:
    run_id, lab, _ = save_run()

    tasks = repo.tasks(run_id)
    assert len(tasks) == 6
    starts = [t["started_at"] for t in tasks]
    assert starts == sorted(starts)  # in execution order
    imaging = repo.tasks(run_id, plate_id="EXP-P001")[1]
    assert (imaging["operation"], imaging["assigned_equipment_id"], imaging["depends_on"]) == \
        ("IMAGE", "IMAGING_01", ["EXP-P001-S01"])
    assert repo.tasks(run_id, status="READY") == []


def test_events_are_saved_and_filterable(repo: RunRepository, save_run) -> None:
    run_id, lab, _ = save_run()

    events = repo.events(run_id)
    assert len(events) == len(lab.recorder.records)
    completed = repo.events(run_id, event_type="TASK_COMPLETED")
    assert len(completed) == 6 and completed[0]["payload"]["task_id"].startswith("EXP-P00")
    assert len(repo.events(run_id, limit=3)) == 3
    assert repo.events(run_id, source="DISPATCHER", event_type="EXPERIMENT_FINISHED")[0]["payload"]["status"] == \
        "COMPLETED"


def test_faults_detections_and_recoveries(repo: RunRepository, save_run) -> None:
    run_id, lab, _ = save_run(
        faults=(FaultSpec(FaultType.TEMPERATURE_EXCURSION, "INCUBATOR_01", 30, duration_min=60),),
    )

    [fault] = repo.faults(run_id)
    assert (fault["fault_type"], fault["equipment_id"], fault["injected_at"]) == \
        ("TEMPERATURE_EXCURSION", "INCUBATOR_01", 30.0)
    [detection] = repo.detections(run_id)
    assert detection["fault_type"] == "TEMPERATURE_EXCURSION" and detection["evidence"]
    [recovery] = repo.recoveries(run_id)
    assert recovery["affected_plates"] == ["EXP-P001", "EXP-P002"]
    assert "back in service" in recovery["actions"]


def test_runs_listing_comparison_and_cascading_delete(repo: RunRepository, save_run) -> None:
    first, _, _ = save_run(label="fifo run")
    second, _, _ = save_run(label="cost run", scheduler="cost")

    assert [r["label"] for r in repo.runs()] == ["fifo run", "cost run"]
    rows = repo.compare_runs([first, second])
    assert [r["scheduler"] for r in rows] == ["fifo", "cost"]
    assert "mean_task_wait_min" in rows[0]

    repo.delete_run(first)
    assert [r["run_id"] for r in repo.runs()] == [second]
    assert repo.tasks(first) == [] and repo.events(first) == []  # children went with it


def test_unknown_run(repo: RunRepository) -> None:
    with pytest.raises(UnknownEntityError, match="run"):
        repo.run(99)
    with pytest.raises(UnknownEntityError):
        repo.delete_run(99)


def test_failed_save_leaves_nothing_behind(repo: RunRepository, make_lab) -> None:
    lab = make_lab()
    lab.schedule_experiment(Experiment("EXP", PROTOCOL, 1, 0.0))
    summary = lab.run()
    lab.state.plates["EXP-P001"].cell_type = None  # type: ignore[assignment]  # violates NOT NULL

    with pytest.raises(sqlite3.IntegrityError):
        repo.save_run(lab, summary, label="broken")

    assert repo.runs() == []  # the whole run was rolled back


def test_database_file_persists_and_checks_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    with Database(path) as database:
        database.connection.execute("UPDATE schema_version SET version = 99")
        database.connection.commit()

    with pytest.raises(ConfigurationError, match="schema version 99"):
        Database(path)


def test_browse_command(tmp_path: Path, make_lab, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "runs.db"
    with Database(path) as database:
        lab = make_lab()
        lab.schedule_experiment(Experiment("EXP", PROTOCOL, 1, 0.0))
        RunRepository(database).save_run(lab, lab.run(), label="demo run")

    assert main([str(path)]) == 0
    assert "demo run" in capsys.readouterr().out
    assert main([str(path), "--run", "1"]) == 0
    assert "Makespan:" in capsys.readouterr().out
    assert main([str(tmp_path / "missing.db")]) == 1
