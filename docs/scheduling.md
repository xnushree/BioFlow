# Protocols, tasks and scheduling

## From protocol to tasks

1. **Protocol** (`protocols/*.yaml`): an ordered list of steps (INCUBATE, MEDIA_EXCHANGE, IMAGE, ARCHIVE,
   DISPOSE) plus culture conditions. Validation reports every problem with its location and a "did you
   mean" hint, while the domain objects enforce the rules themselves (INCUBATE needs a duration,
   ARCHIVE/DISPOSE must be last, TRANSPORT cannot be written in a protocol).
2. **Experiment**: a protocol applied to *n* plates, with a priority and a deadline.
3. **Task graph** (`scheduling/task_graph.py`): one task per plate and step. A plate's steps form a
   chain; a step marked `synchronize: true` waits for the previous step of *every* plate in the
   experiment (a join point, e.g. imaging all plates at the same time point). The graph guarantees
   that a task never becomes READY before all of its dependencies have completed; this is tested with
   150 randomised execution orders. Adding tasks is atomic and rejects duplicates, missing
   dependencies and cycles (Kahn's algorithm).

## Dispatching (`control/dispatcher.py`)

A dispatch pass runs whenever capacity frees up or a task completes:

1. The policy orders the READY tasks.
2. For each task: if the plate is already on the right kind of equipment, run in place. Otherwise
   reserve a destination, then send a robot.
3. `PLATE_PLACED` starts processing; `PROCESSING_COMPLETED` completes the task and releases its
   dependents.

There is no head-of-line blocking: a task that cannot start does not stop the tasks behind it.
Re-entrant dispatch requests are folded into one more pass instead of recursing. When every robot is
busy, the pass only considers tasks that can run in place. This is one of the Phase 27 scaling fixes.

## Policies (`scheduling/`)

A policy answers three questions: *in what order* to try ready tasks, *which* free destination, and
*which* idle robot. FIFO, priority and EDF change only the order and pick the first free resource, so
comparisons between them are fair.

| Policy | Rule |
|---|---|
| `fifo` | Order in which tasks became ready |
| `priority` | Highest experiment priority first, FIFO within a level |
| `deadline` | Earliest deadline first (EDF); experiments without a deadline last |
| `cost` | Lowest dispatch score J, lowest-cost destination, nearest robot |

The cost-based score, with every weight ≥ 0:

```
J = w_travel * travel_min        (robot -> plate -> destination)
  + w_switching * switch         (a station last handled a different cell type)
  - w_delay * waited_min         (time since the task became READY)
  - w_idle * dest_idle_min       (time since the destination was last assigned)
  - w_deadline * critical_ratio  (remaining protocol work / time left before the deadline)
```

Costs add to J; urgency subtracts from it. `CostScheduler.explain(task)` returns the individual terms
behind any decision. The weights in `configs/scheduling.yaml` were found by random search with a
train/test split (Phase 25); see [benchmarking.md](benchmarking.md).

## Resources (`control/resource_manager.py`)

- **Reservations** claim capacity for plates on their way to a destination.
- **Arrival** fulfils a reservation automatically.
- **Conflicts raise errors:** over capacity, double reservation, a faulted destination, a plate
  arriving at the wrong place, or an unreserved plate taking capacity promised to another plate.
- **Out of service:** equipment with a lost link, or awaiting maintenance, can be taken out of service
  without touching its own state machine.
- `RESOURCE_AVAILABLE` announcements trigger new dispatch passes. Waiting is the scheduler's job, not
  the resource manager's, so a first-come-first-served queue can never override the policy.

## Results

Benchmarks across four workloads (light, moderate, failures with tight deadlines, overload) show that
no policy is best everywhere. See [benchmarking.md](benchmarking.md) for the method, the numbers, and the
EDF "domino effect" under overload.
