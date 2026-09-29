# Benchmarking the scheduling policies

## Method

- **Workloads, not fixed scenarios.** `configs/benchmarks.yaml` defines four workload *recipes* (A-D).
  The generator (`src/bioflow/workload.py`) turns a recipe plus a seed into a concrete scenario: random
  experiment arrivals, protocol mix, plate counts, priorities, deadlines relative to each protocol's
  nominal duration, and random equipment faults. Without this, every run of a scenario is
  deterministic and "multiple seeds" would be one sample repeated.
- **Paired comparison.** All four schedulers run on exactly the same generated scenarios. Besides mean
  and standard deviation across seeds, the report counts the seeds on which each policy beat, tied or
  lost to FIFO **on the same workload**.
- **Integrity checks before interpretation.** Every run records completed/total tasks, stalls, faults
  detected versus injected, false alarms and unrecoverable faults. Results are interpreted only
  once these are clean.

Reproduce: `python simulation/benchmark.py` (about 3 minutes). Outputs: `results/benchmarks/*.csv`,
`results/reports/benchmark_report.md`, `results/plots/*.png`.

| Workload | Plates | Equipment | Stress |
|---|---|---|---|
| A | 10 | 2 robots, 2 incubators, 1 imager | light load |
| B | 50 | 3 robots, 4 incubators, 2 imagers | moderate load, deadlines 1.2-1.8x nominal |
| C | 100 | as B | tight deadlines (1.05-1.4x) and 3 random equipment failures |
| D | 220 | 4 robots, 4 incubators, 2 imagers | overload, 6 faults within two hours |

![Benchmark overview](../results/plots/benchmark_overview.png)

## What the benchmark found

**Integrity (after fixes, 80 runs):** all 50,216 tasks completed; 0 stalled runs; 0 false alarms;
176 of 180 injected faults detected. The 4 misses are one imaging failure (workload D, seed 3) seen
by all four schedulers: the imager was not used at all during that failure window, so it had no
observable effect. That is correct behaviour, and it is reported as a miss rather than hidden.

**Policies (5 seeds each, so treat small differences as noise; cost policy with the tuned weights):**

- **Light load (A): no meaningful difference.** With spare capacity, the order in which ready tasks are
  tried barely matters; every policy's means lie well within each other's spread.
- **Moderate load (B): small but consistent effects.** The cost-based policy had a shorter makespan and a
  shorter mean task wait than FIFO on **all 5 seeds**, but only by about 1 % and 17 %. Deadline
  misses do not separate the policies.
- **Tight deadlines with failures (C): the clearest result.** Every non-FIFO policy beat FIFO on most
  seeds: late experiments fell from 71 % (FIFO) to 52-53 %, and mean waiting from 68 to 42-44 min.
  The three smarter policies are indistinguishable from each other within the noise. This is where
  deciding *what* to run next, not just *when*, pays off.
- **Overload (D): the trade-offs show.** With 220 plates the lab cannot meet most deadlines whatever
  the policy. EDF and the cost policy were late on **100 %** of experiments (FIFO 87 %, priority 71 %),
  yet EDF had the **lowest mean lateness** (about 1,090 min vs 1,880 for FIFO). This is the classic
  **EDF domino effect**: when not everything can be on time, always serving the earliest deadline
  spreads the delay so that everything is a little late. Priority scheduling instead sacrifices
  low-priority work and gets more experiments in on time. The cost policy's critical-ratio term
  saturates once experiments are already late, so under overload it behaves like EDF, and it also
  ran about 5 % longer than FIFO here.

**No policy is best everywhere.** Which one to prefer depends on what the lab is judged by: the
share of experiments on time (priority under overload), how late they are (EDF), or waiting and
makespan under moderate stress (cost-based).

## Tuning the cost policy (Phase 25)

`python simulation/tune_cost_weights.py` searches the five weights at random (log-uniform, 30
candidates plus the existing defaults) on workload C.

- **Objective per seed:** fraction of experiments late + (makespan / FIFO makespan - 1). FIFO scores
  exactly its own late fraction.
- **Train/test split:** weights are chosen on training seeds 101-103 and judged only on held-out seeds
  201-205.

| Held-out seeds 201-205, workload C | Objective |
|---|---|
| FIFO | 0.641 |
| cost, previous hand-tuned weights | 0.422 |
| cost, tuned weights | **0.333** |

Before adopting them, the tuned weights were also checked on workloads they were not tuned for (fresh
seeds 301-303): equal on A (0.005 vs 0.005), better on B (0.067 vs 0.098) and D (1.040 vs 1.112).
They are now the default (`configs/scheduling.yaml`).

The picture is honestly **mixed** on one point: on the benchmark's own C seeds 1-5, the tuned weights
were marginally *worse* than the old ones (53 % vs 50 % late), a difference well inside the
seed-to-seed spread. Across all the independent checks the tuned weights were better or equal. Tuning
also did not remove the overload trade-off in D: there the cost policy still trails FIFO on makespan
and on-time rate.

## Bugs the benchmark exposed (and fixes)

Randomised workloads at scale found problems the hand-written scenarios did not:

1. **Deliveries crashed on a closed loading dock.** A communication fault took storage out of service
   exactly when an experiment arrived. Arrivals now wait and retry (`EXPERIMENT_DEFERRED`).
2. **Robots stranded behind a parked robot.** A robot that began waiting while the robot ahead was
   still driving to its parking cell waited forever once it parked (not a deadlock cycle, so
   deadlock detection could not see it). Waiters are now re-evaluated whenever a robot becomes a
   stationary obstacle. A regression test reproduces the exact timing and fails without the fix.
3. **Consequential alarms.** Robots queued behind a broken-down robot were diagnosed as faulty
   themselves and taken out of service. The detector now recognises delays explained by an active
   diagnosis (industrial alarm management calls these *consequential alarms*), excludes that time,
   and, on the map, only calls a transport overdue if the robot has also stopped moving.

## Limitations

- The workloads are synthetic, and the results describe this simulated lab, not a real facility.
- Five seeds per cell is a small sample.
- The cost policy's weights were tuned on one workload with a small search (30 candidates, 3 training seeds).
- Deadlines are set from nominal durations, so the miss rates depend on that choice as much as on the
  scheduler.
