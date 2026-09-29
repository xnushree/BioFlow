"""Database schema.

One row per simulation run in ``runs``; every other table is keyed by
``run_id`` and deleted with its run (ON DELETE CASCADE). The ``tasks`` table
doubles as the *executed schedule*: which equipment ran each task, and when.
"""

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    label             TEXT    NOT NULL,
    scenario          TEXT,
    seed              INTEGER,
    scheduler         TEXT    NOT NULL,
    created_at        TEXT    NOT NULL,
    end_time          REAL    NOT NULL,
    makespan          REAL    NOT NULL,
    tasks_total       INTEGER NOT NULL,
    tasks_completed   INTEGER NOT NULL,
    deadline_misses   INTEGER NOT NULL,
    events_processed  INTEGER NOT NULL,
    stalled           INTEGER NOT NULL,
    metrics_json      TEXT,
    summary_text      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    run_id         INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    experiment_id  TEXT    NOT NULL,
    protocol       TEXT    NOT NULL,
    plate_count    INTEGER NOT NULL,
    priority       INTEGER NOT NULL,
    submitted_at   REAL    NOT NULL,
    deadline       REAL,
    status         TEXT    NOT NULL,
    finished_at    REAL,
    late           INTEGER NOT NULL,
    PRIMARY KEY (run_id, experiment_id)
);

CREATE TABLE IF NOT EXISTS plates (
    run_id         INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    plate_id       TEXT    NOT NULL,
    experiment_id  TEXT    NOT NULL,
    cell_type      TEXT    NOT NULL,
    state          TEXT    NOT NULL,
    location_id    TEXT,
    contamination  TEXT    NOT NULL,
    PRIMARY KEY (run_id, plate_id)
);

CREATE TABLE IF NOT EXISTS equipment (
    run_id         INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    equipment_id   TEXT    NOT NULL,
    kind           TEXT    NOT NULL,
    state          TEXT    NOT NULL,
    utilization    REAL,
    snapshot_json  TEXT    NOT NULL,
    PRIMARY KEY (run_id, equipment_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    run_id                 INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    task_id                TEXT    NOT NULL,
    experiment_id          TEXT    NOT NULL,
    plate_id               TEXT    NOT NULL,
    operation              TEXT    NOT NULL,
    step                   INTEGER,
    status                 TEXT    NOT NULL,
    assigned_equipment_id  TEXT,
    duration_min           REAL,
    ready_at               REAL,
    started_at             REAL,
    completed_at           REAL,
    depends_on_json        TEXT    NOT NULL,
    PRIMARY KEY (run_id, task_id)
);

CREATE TABLE IF NOT EXISTS events (
    run_id        INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    event_id      TEXT    NOT NULL,
    sim_time      REAL    NOT NULL,
    event_type    TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    target        TEXT,
    payload_json  TEXT    NOT NULL,
    PRIMARY KEY (run_id, event_id)
);
CREATE INDEX IF NOT EXISTS events_by_type ON events (run_id, event_type, sim_time);

CREATE TABLE IF NOT EXISTS faults (
    run_id        INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    fault_id      TEXT    NOT NULL,
    fault_type    TEXT    NOT NULL,
    equipment_id  TEXT    NOT NULL,
    severity      TEXT    NOT NULL,
    start_min     REAL    NOT NULL,
    duration_min  REAL,
    injected_at   REAL,
    repaired_at   REAL,
    PRIMARY KEY (run_id, fault_id)
);

CREATE TABLE IF NOT EXISTS detections (
    run_id        INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    detection_id  TEXT    NOT NULL,
    fault_type    TEXT    NOT NULL,
    equipment_id  TEXT    NOT NULL,
    category      TEXT    NOT NULL,
    detected_at   REAL    NOT NULL,
    cleared_at    REAL,
    evidence      TEXT    NOT NULL,
    PRIMARY KEY (run_id, detection_id)
);

CREATE TABLE IF NOT EXISTS recoveries (
    run_id                INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    recovery_id           TEXT    NOT NULL,
    detection_id          TEXT    NOT NULL,
    fault_type            TEXT    NOT NULL,
    equipment_id          TEXT    NOT NULL,
    started_at            REAL    NOT NULL,
    completed_at          REAL,
    unrecoverable         INTEGER NOT NULL,
    blocked_reason        TEXT,
    rescheduled_tasks     INTEGER NOT NULL,
    affected_plates_json  TEXT    NOT NULL,
    actions_json          TEXT    NOT NULL,
    PRIMARY KEY (run_id, recovery_id)
);
"""
