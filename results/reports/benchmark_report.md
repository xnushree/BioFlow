# Scheduler benchmark report

80 simulation runs: 4 workloads x 4 schedulers x 5 seeds (seeds 1, 2, 3, 4, 5), 162 s wall time.
Every scheduler ran on exactly the same generated scenarios, so comparisons are paired.

Values are **mean ± standard deviation across seeds**. Where two schedulers' means differ by less than their spread, the data does not show a real difference.

## Workload A

Light load - 10 plates, 2 robots, 2 incubators, 1 imaging station.

| Scheduler | Makespan (min) | Mean task wait (min) | Max task wait (min) | Experiments late (fraction) | Mean lateness of late experiments (min) | Throughput (plates/hour) | Robot utilization | Station utilization | Mean ready queue | Tasks rescheduled | Mean recovery time (min) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fifo | 2,211 ± 351 | 13 ± 5.51 | 112 ± 45 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.28 ± 0.06 | 0.04 ± 0.02 | 0.09 ± 0.05 | 0.39 ± 0.31 | 0.00 ± 0.00 | 0.00 ± 0.00 |
| priority | 2,216 ± 340 | 14 ± 6.77 | 136 ± 49 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.28 ± 0.05 | 0.04 ± 0.01 | 0.09 ± 0.05 | 0.41 ± 0.37 | 0.00 ± 0.00 | 0.00 ± 0.00 |
| deadline | 2,211 ± 351 | 12 ± 4.48 | 133 ± 44 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.28 ± 0.06 | 0.04 ± 0.02 | 0.09 ± 0.05 | 0.36 ± 0.26 | 0.00 ± 0.00 | 0.00 ± 0.00 |
| cost | 2,211 ± 352 | 12 ± 4.48 | 133 ± 44 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.28 ± 0.06 | 0.04 ± 0.02 | 0.09 ± 0.05 | 0.36 ± 0.26 | 0.00 ± 0.00 | 0.00 ± 0.00 |

Integrity: 1152/1152 tasks completed across all runs, 0 stalled runs, 0/0 injected faults detected, 0 false alarms, 0 unrecoverable.

## Workload B

Moderate load - 50 plates, 3 robots, 4 incubators, 2 imaging stations.

| Scheduler | Makespan (min) | Mean task wait (min) | Max task wait (min) | Experiments late (fraction) | Mean lateness of late experiments (min) | Throughput (plates/hour) | Robot utilization | Station utilization | Mean ready queue | Tasks rescheduled | Mean recovery time (min) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fifo | 2,588 ± 72 | 16 ± 11 | 187 ± 120 | 0.24 ± 0.15 | 150 ± 116 | 1.16 ± 0.03 | 0.14 ± 0.01 | 0.22 ± 0.02 | 2.18 ± 1.57 | 0.00 ± 0.00 | 0.00 ± 0.00 |
| priority | 2,577 ± 51 | 15 ± 9.49 | 207 ± 119 | 0.19 ± 0.17 | 162 ± 142 | 1.16 ± 0.02 | 0.14 ± 0.01 | 0.22 ± 0.03 | 2.07 ± 1.38 | 0.00 ± 0.00 | 0.00 ± 0.00 |
| deadline | 2,573 ± 45 | 13 ± 8.28 | 222 ± 126 | 0.22 ± 0.17 | 89 ± 80 | 1.17 ± 0.02 | 0.14 ± 0.01 | 0.22 ± 0.03 | 1.84 ± 1.22 | 0.00 ± 0.00 | 0.00 ± 0.00 |
| cost | 2,564 ± 34 | 13 ± 7.40 | 214 ± 132 | 0.22 ± 0.17 | 98 ± 90 | 1.17 ± 0.02 | 0.13 ± 0.01 | 0.22 ± 0.03 | 1.83 ± 1.12 | 0.00 ± 0.00 | 0.00 ± 0.00 |

Integrity: 6952/6952 tasks completed across all runs, 0 stalled runs, 0/0 injected faults detected, 0 false alarms, 0 unrecoverable.

## Workload C

100 plates with tight deadlines and three random equipment failures.

| Scheduler | Makespan (min) | Mean task wait (min) | Max task wait (min) | Experiments late (fraction) | Mean lateness of late experiments (min) | Throughput (plates/hour) | Robot utilization | Station utilization | Mean ready queue | Tasks rescheduled | Mean recovery time (min) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fifo | 3,398 ± 267 | 68 ± 26 | 688 ± 270 | 0.71 ± 0.25 | 351 ± 186 | 1.78 ± 0.15 | 0.19 ± 0.01 | 0.31 ± 0.02 | 13 ± 5.03 | 2.00 ± 1.00 | 121 ± 13 |
| priority | 3,061 ± 105 | 44 ± 14 | 827 ± 439 | 0.52 ± 0.20 | 326 ± 179 | 1.96 ± 0.07 | 0.21 ± 0.01 | 0.34 ± 0.03 | 9.45 ± 3.14 | 2.40 ± 0.55 | 112 ± 21 |
| deadline | 3,013 ± 176 | 43 ± 17 | 565 ± 210 | 0.53 ± 0.23 | 215 ± 98 | 2.00 ± 0.11 | 0.22 ± 0.02 | 0.35 ± 0.04 | 9.21 ± 3.69 | 2.20 ± 0.84 | 115 ± 24 |
| cost | 3,000 ± 133 | 44 ± 17 | 501 ± 128 | 0.53 ± 0.20 | 242 ± 136 | 2.00 ± 0.09 | 0.21 ± 0.02 | 0.35 ± 0.04 | 9.55 ± 3.85 | 2.60 ± 0.55 | 114 ± 26 |

Integrity: 12928/12928 tasks completed across all runs, 0 stalled runs, 60/60 injected faults detected, 0 false alarms, 0 unrecoverable.

## Workload D

220 plates, 4 robots, and six faults striking within the same two hours.

| Scheduler | Makespan (min) | Mean task wait (min) | Max task wait (min) | Experiments late (fraction) | Mean lateness of late experiments (min) | Throughput (plates/hour) | Robot utilization | Station utilization | Mean ready queue | Tasks rescheduled | Mean recovery time (min) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fifo | 5,390 ± 260 | 305 ± 28 | 2,492 ± 270 | 0.87 ± 0.04 | 1,883 ± 305 | 2.45 ± 0.12 | 0.22 ± 0.01 | 0.47 ± 0.01 | 88 ± 6.84 | 14 ± 18 | 128 ± 28 |
| priority | 5,487 ± 488 | 219 ± 36 | 3,497 ± 285 | 0.71 ± 0.12 | 1,641 ± 232 | 2.42 ± 0.21 | 0.22 ± 0.02 | 0.46 ± 0.05 | 62 ± 6.82 | 14 ± 17 | 127 ± 29 |
| deadline | 5,436 ± 225 | 233 ± 17 | 2,811 ± 204 | 1.00 ± 0.00 | 1,088 ± 167 | 2.43 ± 0.10 | 0.22 ± 0.01 | 0.47 ± 0.03 | 67 ± 2.59 | 18 ± 18 | 125 ± 29 |
| cost | 5,642 ± 380 | 257 ± 23 | 2,995 ± 401 | 1.00 ± 0.00 | 1,320 ± 274 | 2.35 ± 0.16 | 0.20 ± 0.01 | 0.45 ± 0.02 | 71 ± 3.88 | 14 ± 16 | 127 ± 29 |

Integrity: 31184/31184 tasks completed across all runs, 0 stalled runs, 116/120 injected faults detected, 0 false alarms, 0 unrecoverable.

## Paired comparison against FIFO

On how many seeds each scheduler did better / the same / worse than FIFO on the same workload.

| Workload | Scheduler | Makespan (min) | Mean task wait (min) | Experiments late (fraction) |
|---|---|---|---|---|
| A | cost | 2 / 0 / 3 | 3 / 0 / 2 | 0 / 5 / 0 |
| A | deadline | 1 / 3 / 1 | 2 / 3 / 0 | 0 / 5 / 0 |
| A | priority | 0 / 3 / 2 | 1 / 3 / 1 | 0 / 5 / 0 |
| B | cost | 5 / 0 / 0 | 5 / 0 / 0 | 1 / 4 / 0 |
| B | deadline | 2 / 2 / 1 | 5 / 0 / 0 | 1 / 4 / 0 |
| B | priority | 2 / 2 / 1 | 4 / 0 / 1 | 3 / 2 / 0 |
| C | cost | 4 / 0 / 1 | 4 / 0 / 1 | 5 / 0 / 0 |
| C | deadline | 4 / 0 / 1 | 5 / 0 / 0 | 4 / 0 / 1 |
| C | priority | 4 / 0 / 1 | 4 / 0 / 1 | 5 / 0 / 0 |
| D | cost | 1 / 0 / 4 | 5 / 0 / 0 | 0 / 0 / 5 |
| D | deadline | 2 / 0 / 3 | 5 / 0 / 0 | 0 / 0 / 5 |
| D | priority | 1 / 0 / 4 | 5 / 0 / 0 | 4 / 1 / 0 |

## Caveats

- Workloads are synthetic and generated from the recipes in `configs/benchmarks.yaml`; results describe this simulated lab, not any real facility.
- 5 seeds per cell is a small sample; treat differences within one standard deviation as noise.
- The cost-based scheduler uses weights tuned on workload C (`configs/scheduling.yaml`, see docs/benchmarking.md).
- Deadlines are set relative to each protocol's nominal duration; the miss rate depends on that choice as much as on the scheduler.

## Charts

![Overview](../plots/benchmark_overview.png)
