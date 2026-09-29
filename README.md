# BioFlow-X

**A fault-tolerant digital twin and supervisory control platform for a simulated cell-culture laboratory.**

BioFlow-X simulates an automated cell-culture lab (transport robots, incubators, media-exchange and imaging
stations, storage) and the software that runs it: protocol-driven task graphs, pluggable scheduling policies,
A* path planning with multi-robot deadlock handling, fault injection, symptom-based fault detection and
automatic recovery, plus telemetry, persistence, a REST API and a live dashboard.

It is an original, fully software-based engineering project. No physical equipment or biological samples are
involved, and the biological quantities (temperature, CO2, incubation times) are abstract workflow
constraints, not a model of cell growth.

> **Status: in development.** Phases 0-23 of 31 are complete and tested (739 automated tests).
> Benchmarking, optimisation, documentation and the final demonstration are still to come,
> and this README will be replaced by a full one.

## What works today

| Area | Highlights |
|---|---|
| Simulation core | Priority-queue discrete-event engine, deterministic and seeded, runs much faster than real time |
| Domain & state machines | Plates, experiments, protocols and tasks with explicit, exhaustively tested transition tables |
| Protocols | YAML protocols validated with located, "did you mean" error messages; synchronised steps create join points |
| Scheduling | FIFO, priority, earliest-deadline-first and a configurable cost-based policy |
| Robotics | Grid floor plan, A* planning, cell reservations (collision-free), deadlock detection and recovery |
| Faults | 10 injectable fault types; detection from observable symptoms only; automatic recovery with safe holds |
| Platform | Telemetry and metrics, SQLite persistence, FastAPI REST API, Streamlit dashboard with a live 2D twin |

## Quick start (Windows, Python 3.11+)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e .

pytest -q                                                                   # run the test suite
python simulation/run_simulation.py simulation/scenarios/basic_demo.yaml   # run a scenario
python simulation/run_simulation.py simulation/scenarios/multiple_failures.yaml   # 10 faults, detected and recovered
streamlit run dashboard/app.py                                              # dashboard
uvicorn bioflow.api.main:app                                                # REST API (docs at /docs)
```

## Project layout

```
src/bioflow/     core engine, domain, equipment, control, scheduling, robotics, faults,
                 telemetry, database, api, analytics
configs/         equipment, laboratory floor plan, scheduling weights, fault settings
protocols/       experiment protocols (YAML)
simulation/      scenario files and command-line runners
dashboard/       Streamlit dashboard
tests/           unit, integration and system tests
```

## Author

Anushree Verma
