# Performance and scale (Phase 27)

`simulation/scale_test.py` generates one long, near-capacity workload per size (a 2:1 mix of the basic
and imaging protocols; 4 robots on the floor plan, 4 incubators, 2 media and 2 imaging stations;
monitoring and fault detection on) and measures wall-clock time, events per second, and the time spent
inside scheduling decisions. Plates arrive about every 40 minutes, roughly 80 % of what the lab can
process, so queues build up without growing without limit.

```powershell
python simulation/scale_test.py                          # 100 .. 5000 plates -> results/reports/scale_report.md
python simulation/scale_test.py --sizes 500 --profile    # cProfile hotspots
python simulation/scale_test.py --sizes 100 1000 --memory  # peak Python heap (tracemalloc, slower)
```

## Results

Measured on the development laptop (Windows 10, Python 3.13, one core), scheduler `fifo`, seed 1:

| Plates | Tasks (all completed) | Simulated time | Events | Wall time | Events/s | Of which scheduling |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 572 | 3.5 days | 44,517 | 1.2 s | 36,000 | 0.2 s |
| 500 | 2,800 | 14.8 days | 214,247 | 5.5 s | 39,000 | 0.9 s |
| 1,000 | 5,676 | 28.1 days | 432,665 | 11.0 s | 39,000 | 2.0 s |
| 2,000 | 11,222 | 55.8 days | 854,870 | 22.1 s | 38,700 | 4.0 s |
| 5,000 | 28,434 | 139.7 days | 2,172,951 | 59.2 s | 36,700 | 11.7 s |

Wall time grows **linearly** with workload size (events/s is flat from 100 to 5,000 plates), and
140 simulated days of a busy lab run in about a minute. Scheduling stays at about 20 % of runtime.
Everything else is the event machinery itself: bus delivery, robot motion steps and monitoring.

Peak Python heap (a separate `--memory` pass) is also linear: 2 MB at 100 plates, 19 MB at 1,000 and
90 MB at 5,000 plates, about 18 KB per plate.

## What profiling found

The first attempt at 1,000 plates **never finished**. Profiling and tracing found one correctness bug and
three performance problems:

1. **A real livelock (correctness).** Archived plates stayed in their storage slots forever. After a few
   hundred plates, storage (capacity 500) was full of finished work: new arrivals were deferred
   indefinitely, and ARCHIVE tasks could never get a slot, so the simulation ticked on with no progress.
   The fix models what a real lab does: archiving moves a plate to an off-site archive and disposal
   sends it to waste, so it **leaves the automated system** and frees its slot. In addition, an
   experiment whose delivery cannot be accepted for 7 days is **rejected** and reported, rather than
   retried forever. Tests cover both.
2. **A\* recomputed for every trip.** Static routes between the same two access points were planned
   again every time; before the fix, path planning took about a quarter of total runtime in the profile.
   Static paths are now cached (the map is immutable). Replanning around *other robots* is still done
   live.
3. **Futile dispatch work.** When every robot was busy, each dispatch pass still evaluated every ready
   task that needed a transport. Such tasks are now skipped until a robot frees up, and the free robot
   count is refreshed after each start.
4. **Linear scans per event.** "Is there work remaining?" and "Is this experiment finished?" scanned all
   tasks. They are now O(1), using counters and a per-experiment index maintained by the task graph.

After fixes 2-4, throughput rose from about 25,000 to about 39,000 events/s. At 500 plates, scheduling
time fell from 2.5 s to 0.9 s. (The "before" run used slightly denser arrivals, so the per-event rate is
the fair comparison.) Every regression scenario produced *identical* results before and after, so the
optimisations changed speed only, not behaviour.

## Limits

- It is single-threaded, pure Python, and there is no attempt at parallelism. Independent runs
  (benchmarks, tuning) are embarrassingly parallel, but are run one at a time for simplicity.
- Memory grows with the number of tasks, because completed tasks and plates are kept for the final
  report. That is deliberate for a simulator whose output *is* the history. `--memory` measures it.
- Telemetry recording (off in these runs) adds a per-event cost. `standard` level keeps it bounded.
