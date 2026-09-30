# Resume bullets

Every number below comes from a report in `results/reports/` and can be reproduced with the scripts in
`simulation/`. Pick three or four bullets; do not list every technology.

**BioFlow-X: Fault-Tolerant Cell-Culture Automation Digital Twin** · Python, FastAPI, SQLite, Streamlit, pytest
[github.com/xnushree/BioFlow](https://github.com/xnushree/BioFlow)

- Built a discrete-event **digital twin** of an automated cell-culture lab (robots, incubators, processing
  stations) in Python, with deterministic seeded simulation that runs 140 simulated days (5,000 plates,
  2.2 M events) in about a minute, scaling linearly.
- Designed an **event-driven supervisory control** layer: YAML protocols compiled to task dependency graphs,
  four interchangeable scheduling policies (FIFO, priority, EDF, weighted cost), and resource reservations
  so no transport starts without a guaranteed destination.
- Implemented **multi-robot coordination** on a grid map: A\* planning, cell reservations (collision-free by
  construction), and wait-for-graph deadlock detection with escalating resolution.
- Engineered **fault tolerance**: 10 injectable fault types detected only from observable symptoms
  (heartbeats, noisy sensors, timing), with automatic recovery. Across 80 randomised runs, 176/180 faults
  were detected, 0 false alarms were raised, and every task completed.
- Ran **paired multi-seed benchmarks** and tuned the cost policy by random search with held-out seeds. Under
  tight deadlines with failures, the late-experiment rate fell from 71 % (FIFO) to 52-53 %, and the held-out
  objective improved by 48 % over FIFO.
- Profiled the simulator at scale and found and fixed a storage livelock and three hotspots, raising
  throughput from about 25k to 39k events/s.
- Exposed the live system through a **FastAPI REST API** and a **Streamlit dashboard** with a 2D twin view,
  backed by SQLite persistence; 778 tests, 97 % coverage.

Shorter variants:

- Digital twin + supervisory control for a simulated lab automation cell: scheduling, A\* multi-robot
  coordination, symptom-based fault detection and automatic recovery (Python, FastAPI, Streamlit).
- Benchmarked 4 scheduling policies across 80 paired simulation runs; tuned weights generalised to held-out
  workloads (objective 0.64 → 0.33).
