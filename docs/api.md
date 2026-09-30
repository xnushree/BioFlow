# REST API

Start the server from the project root, then open the interactive documentation at
<http://127.0.0.1:8000/docs>:

```powershell
uvicorn bioflow.api.main:app
# optionally preload a scenario:
$env:BIOFLOW_SCENARIO="simulation/scenarios/basic_demo.yaml"; uvicorn bioflow.api.main:app
```

The API only adapts HTTP to `SimulationService` (`service.py`); it contains no simulation logic. The
service runs one live laboratory in a background thread, in slices of simulated time, and serialises
every read with a lock so responses are consistent snapshots.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness and version |
| GET | `/simulation/status` | State (EMPTY/READY/RUNNING/PAUSED/FINISHED), simulated time, progress, background errors |
| POST | `/simulation/load` | Load a scenario `{"scenario": "...yaml", "scheduler": "cost"}` |
| POST | `/simulation/start` | Run in the background `{"speed": 60}` (simulated minutes per real second; omit for flat out) |
| POST | `/simulation/stop` | Pause |
| POST | `/simulation/step` | Advance synchronously `{"minutes": 30}` (only while paused) |
| GET | `/experiments` | All experiments with status and task counts |
| POST | `/experiments` | Submit now `{"experiment_id", "protocol", "plates", "priority", "deadline_in_min"}` |
| GET | `/experiments/{id}` | Protocol steps, plates and progress |
| GET | `/plates` | Filter by `experiment_id` and `state` |
| GET | `/equipment`, `/equipment/{id}` | State, occupancy, operational and in-service flags, active detections |
| GET | `/robots` | Robots with grid cell and remaining planned path |
| GET | `/faults` | Detections, recoveries, and labelled injected ground truth |
| POST | `/faults/inject` | Inject now `{"type", "equipment", "duration_min", "severity", "magnitude", "mode"}` |
| GET | `/metrics` | Utilization, queue, waits, throughput, reservations, bus statistics |
| GET | `/events` | Recent recorded events, filterable by `type` and `source`, with a `limit` |

## Errors

Platform errors map to HTTP status codes with `{"error": <type>, "detail": <message>}`:

| Error | Status |
|---|---|
| Unknown experiment, plate, equipment or protocol | 404 |
| Resource conflict, safety refusal, invalid transition, simulation misuse (e.g. stepping while running) | 409 |
| Domain validation or protocol errors | 422 (request-body validation errors are also 422) |
| Configuration problems (e.g. nothing loaded yet) | 400 |

## Example session

```powershell
curl -X POST localhost:8000/simulation/load -H "content-type: application/json" `
     -d '{"scenario": "simulation/scenarios/basic_demo.yaml"}'
curl -X POST localhost:8000/simulation/step -H "content-type: application/json" -d '{"minutes": 730}'
curl -X POST localhost:8000/faults/inject -H "content-type: application/json" `
     -d '{"type": "TEMPERATURE_EXCURSION", "equipment": "INCUBATOR_01", "duration_min": 60}'
curl -X POST localhost:8000/simulation/step -H "content-type: application/json" -d '{"minutes": 120}'
curl "localhost:8000/faults"
```
