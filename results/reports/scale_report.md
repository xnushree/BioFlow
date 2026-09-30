# Scale test

Scheduler `fifo`, seed 1, arrivals every ~40 min per plate (near lab capacity), 4 robots on the map with monitoring and fault detection on.

| plates | tasks | completed | sim_days | events | bus_messages | wall_s | events_per_s | scheduling_s |
|---|---|---|---|---|---|---|---|---|
| 100 | 572 | 572 | 3.5 | 44,517 | 132,046 | 1.2 | 36,233.4 | 0.2 |
| 500 | 2,800 | 2,800 | 14.8 | 214,247 | 592,723 | 5.5 | 39,053.8 | 0.9 |
| 1,000 | 5,676 | 5,676 | 28.1 | 432,665 | 1,158,394 | 11.0 | 39,164.6 | 2.0 |
| 2,000 | 11,222 | 11,222 | 55.8 | 854,870 | 2,294,325 | 22.1 | 38,702.0 | 4.0 |
| 5,000 | 28,434 | 28,434 | 139.7 | 2,172,951 | 5,798,826 | 59.2 | 36,696.8 | 11.7 |

## Peak memory

Separate pass with `--memory` (tracemalloc, which slows the run; wall times from that pass are not comparable, and the 5,000-plate pass also shared the CPU with the test suite).

| plates | peak Python heap (MB) |
|---|---|
| 100 | 2.1 |
| 1,000 | 18.6 |
| 5,000 | 90.5 |

Memory grows linearly, about 18 KB per plate, because completed tasks and plate histories are kept for the final report.
