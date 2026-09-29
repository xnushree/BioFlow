"""REST API tests: HTTP requests against the real service and simulation (no mocks)."""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bioflow.api.main import create_app
from bioflow.service import SimulationService

ROOT = Path(__file__).parents[2]
DEMO = "simulation/scenarios/basic_demo.yaml"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.chdir(ROOT)
    svc = SimulationService()
    with TestClient(create_app(svc)) as test_client:
        yield test_client
    svc.pause()  # never leave a background thread running after a test


@pytest.fixture
def loaded(client: TestClient) -> TestClient:
    assert client.post("/simulation/load", json={"scenario": DEMO}).status_code == 200
    return client


def test_health(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_nothing_loaded_yet(client: TestClient) -> None:
    assert client.get("/simulation/status").json()["state"] == "EMPTY"
    response = client.get("/equipment")
    assert response.status_code == 400
    assert "POST /simulation/load" in response.json()["detail"]


def test_load_and_step(loaded: TestClient) -> None:
    status = loaded.get("/simulation/status").json()
    assert (status["state"], status["scenario"], status["sim_time"]) == ("READY", "basic_demo", 0.0)

    stepped = loaded.post("/simulation/step", json={"minutes": 120}).json()

    assert stepped["state"] == "PAUSED" and stepped["sim_time"] == 120.0
    assert stepped["events_processed"] > 0  # (no task has finished yet: the first step is a 720-min incubation)


def test_load_with_scheduler_override(client: TestClient) -> None:
    status = client.post("/simulation/load", json={"scenario": DEMO, "scheduler": "cost"}).json()

    assert status["scheduler"] == "cost"


def test_background_run_to_completion(loaded: TestClient) -> None:
    # Flat out, the demo can finish before the response is even built.
    assert loaded.post("/simulation/start", json={}).json()["state"] in ("RUNNING", "FINISHED")

    deadline = time.monotonic() + 30
    while loaded.get("/simulation/status").json()["state"] != "FINISHED":
        assert time.monotonic() < deadline, "background simulation did not finish"
        time.sleep(0.05)

    status = loaded.get("/simulation/status").json()
    assert status["tasks_completed"] == status["tasks_total"] == 78
    assert status["error"] is None


def test_paced_run_can_be_paused(loaded: TestClient) -> None:
    loaded.post("/simulation/start", json={"speed": 600})  # 10 simulated hours per real second
    time.sleep(0.3)
    paused = loaded.post("/simulation/stop").json()

    assert paused["state"] == "PAUSED"
    assert 0 < paused["sim_time"] < 2400
    assert loaded.post("/simulation/step", json={"minutes": 5}).json()["sim_time"] == pytest.approx(
        paused["sim_time"] + 5)


def test_step_while_running_is_a_conflict(loaded: TestClient) -> None:
    loaded.post("/simulation/start", json={"speed": 1})
    response = loaded.post("/simulation/step", json={"minutes": 5})

    assert response.status_code == 409


def test_experiments_and_plates(loaded: TestClient) -> None:
    loaded.post("/simulation/step", json={"minutes": 100})

    experiments = {e["experiment_id"]: e for e in loaded.get("/experiments").json()}
    assert set(experiments) == {"EXP001", "EXP002"}
    exp = loaded.get("/experiments/EXP001").json()
    assert exp["protocol"] == "basic_experiment" and len(exp["plates"]) == 10
    assert exp["steps"][0] == {"operation": "INCUBATE", "duration_min": 720, "synchronize": False}
    plates = loaded.get("/plates", params={"experiment_id": "EXP002"}).json()
    assert len(plates) == 4
    assert loaded.get("/experiments/NOPE").status_code == 404


def test_submit_experiment_mid_run(loaded: TestClient) -> None:
    loaded.post("/simulation/step", json={"minutes": 50})

    created = loaded.post("/experiments", json={"experiment_id": "EXP100", "protocol": "stress_test",
                                                 "plates": 2, "priority": 5, "deadline_in_min": 600})
    assert created.status_code == 201 and created.json()["submitted_at"] == 50.0
    loaded.post("/simulation/step", json={"minutes": 1})

    exp = loaded.get("/experiments/EXP100").json()
    assert exp["deadline"] == 650.0 and exp["priority"] == 5


@pytest.mark.parametrize(("body", "status"), [
    ({"experiment_id": "EXP001", "protocol": "basic_experiment", "plates": 1}, 422),  # duplicate
    ({"experiment_id": "X", "protocol": "nope", "plates": 1}, 404),
    ({"experiment_id": "X", "protocol": "basic_experiment", "plates": 0}, 422),  # request validation
])
def test_invalid_experiment_submissions(loaded: TestClient, body: dict, status: int) -> None:
    loaded.post("/simulation/step", json={"minutes": 100})

    assert loaded.post("/experiments", json=body).status_code == status


def test_equipment_and_robots(loaded: TestClient) -> None:
    loaded.post("/simulation/step", json={"minutes": 30})

    equipment = {e["equipment_id"]: e for e in loaded.get("/equipment").json()}
    assert equipment["INCUBATOR_01"]["kind"] == "INCUBATOR"
    assert equipment["INCUBATOR_01"]["operational"] and equipment["INCUBATOR_01"]["in_service"]
    assert loaded.get("/equipment/IMAGING_01").json()["operation"] == "IMAGE"
    assert loaded.get("/equipment/LASER_01").status_code == 404
    robots = loaded.get("/robots").json()
    assert len(robots) == 2 and all(len(r["cell"]) == 2 for r in robots)


def test_inject_fault_is_detected_and_recovered(loaded: TestClient) -> None:
    loaded.post("/simulation/step", json={"minutes": 100})

    injected = loaded.post("/faults/inject", json={"type": "TEMPERATURE_EXCURSION", "equipment": "INCUBATOR_01",
                                                    "duration_min": 60, "severity": "HIGH"})
    assert injected.status_code == 201 and injected.json()["starts_at"] == 100.0
    loaded.post("/simulation/step", json={"minutes": 200})

    faults = loaded.get("/faults").json()
    assert faults["injected_ground_truth"][0]["fault_type"] == "TEMPERATURE_EXCURSION"
    assert faults["detections"][0]["fault_type"] == "TEMPERATURE_EXCURSION"
    assert faults["recoveries"][0]["completed_at"] is not None


@pytest.mark.parametrize("body", [
    {"type": "BAD_TYPE", "equipment": "INCUBATOR_01"},
    {"type": "IMAGING_FAILURE", "equipment": "ROBOT_01"},
])
def test_invalid_fault_injection(loaded: TestClient, body: dict) -> None:
    assert loaded.post("/faults/inject", json=body).status_code == 422


def test_metrics_and_events(loaded: TestClient) -> None:
    loaded.post("/simulation/step", json={"minutes": 800})

    metrics = loaded.get("/metrics").json()
    assert 0 < metrics["mean_robot_utilization"] < 1
    assert {r["resource_id"] for r in metrics["resources"]} >= {"INCUBATOR_01", "IMAGING_01"}
    events = loaded.get("/events", params={"type": "TASK_COMPLETED", "limit": 5}).json()
    assert len(events) == 5 and all(e["event_type"] == "TASK_COMPLETED" for e in events)
    assert [e["sim_time"] for e in events] == sorted(e["sim_time"] for e in events)


def test_openapi_documents_every_endpoint(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])

    assert paths >= {"/health", "/simulation/status", "/simulation/load", "/simulation/start", "/simulation/stop",
                     "/simulation/step", "/experiments", "/experiments/{experiment_id}", "/plates", "/equipment",
                     "/equipment/{equipment_id}", "/robots", "/faults", "/faults/inject", "/metrics", "/events"}
