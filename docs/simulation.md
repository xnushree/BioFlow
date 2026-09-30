# Simulation and the digital twin

## Discrete-event engine (`core/simulation.py`)

The engine keeps a priority queue of future events ordered by `(timestamp, sequence)`. `step()` pops the
earliest event, advances the clock to its timestamp and calls the handler registered with it. Handlers
change state and usually schedule further events, which is how the simulation moves forward.

- **Time is data.** The clock jumps from event to event, so 40 simulated hours take milliseconds, and
  nothing ever waits on the wall clock.
- **Deterministic ordering.** Events at the same timestamp run in the order they were scheduled, and
  every random draw comes from one seeded generator, so the same seed and configuration always
  reproduce the same run.
- **Cancellation** is lazy (the entry is marked and skipped when popped), which keeps it O(1). Fault
  handling relies on it: a robot that breaks down mid-step must never fire its "step finished" event.
- **Safety stops:** `run(until=...)`, `max_events` against runaway event loops, and `stop()`.
- **The clock only moves forward.** Going backwards means an event was processed out of order (a
  causality violation), so it raises immediately. NaN and infinite times are rejected too.

## Event bus (`core/event_bus.py`)

Publish/subscribe with **causal FIFO delivery**. An event published while another is being delivered
is queued until the first has reached every subscriber. The engine's first design used nested delivery,
which let a late subscriber see "robot started job 2" before "robot finished job 1". That real bug
(found in Phase 16) is why the bus was changed. Subscriptions can be filtered and can be marked
non-critical (isolated observers). The bus keeps per-type counts and error statistics.

## The digital twin

The twin is the live state of the lab, and it is the single thing that interfaces read:

| Aspect | Where it lives |
|---|---|
| Layout (grid, footprints, access points, zones, parking) | `robotics/map.py` (from `configs/laboratory.yaml`) |
| Equipment state and occupancy | each equipment object (`equipment/`) |
| Robot positions and planned paths | `robotics/motion.py` (`GridMotion`) |
| Plates, experiments, tasks | `control/state_manager.py` and the task graph |
| Reservations | `control/resource_manager.py` |
| Environment | incubator sensor readings (the twin shows *observed* values, not hidden truth) |
| Faults | detector and recovery records; injected ground truth kept separately |

The dashboard's 2D view is drawn only from this state: equipment colours come from state machines,
robots are drawn at the cells they hold, paths are their remaining A* plans, and counts are real
occupancy.

## Equipment and state machines

Every piece of equipment has an explicit transition table (`core/state_machine.py` provides the
mechanism; each table lives next to its equipment). Examples:

```
Robot:     IDLE -> ASSIGNED -> MOVING -> PICKING -> TRANSPORTING -> PLACING -> IDLE
           any -> FAULT -> RECOVERY -> (resume the frozen phase, or IDLE)
           MOVING/TRANSPORTING -> SAFE_STOP -> RECOVERY
Incubator: AVAILABLE <-> FULL;  AVAILABLE/FULL -> ENVIRONMENTAL_FAULT -> RECOVERY;  any -> FAULT
Station:   IDLE <-> OCCUPIED <-> PROCESSING;  any -> FAULT -> RECOVERY -> IDLE/OCCUPIED
Plate:     CREATED -> STORED -> IN_TRANSIT -> WAITING <-> INCUBATING/PROCESSING ... -> ARCHIVED/DISPOSED
```

Tables are validated when they are built (no forgotten state, no self-loops, terminal states have no
exits), and tests check each table against the complete list of legal moves, so every other pair is
proven illegal.

When a plate is archived or disposed of it **leaves the automated lab** (off-site archive, or waste),
freeing its slot. An earlier design kept finished plates in their slots; the large-scale test showed
this fills storage and locks up long runs (Phase 27).

## Robot motion (`robotics/`)

- **Floor plan:** a grid with equipment footprints, access points, blocked cells, slow zones (cost of 1 or
  more, so Manhattan distance stays an admissible A* heuristic) and parking cells. It is validated for
  overlaps and reachability.
- **A\*** finds the cheapest path; it has been verified against Dijkstra on random maps. Static paths
  are cached.
- **Cell reservations:** a robot must hold its cell and acquire the next one before moving, so two
  robots can never occupy the same cell.
- **Waiting and deadlocks:** blocked robots wait. Every new wait triggers cycle detection on the
  wait-for graph. Recovery escalates in order: replan around other robots, step into a side cell,
  back off. A livelock guard stops safely if the same robots keep deadlocking.
- **Stationary obstacles:** idle robots park out of the way and are nudged if they block a route. When a
  robot becomes a stationary obstacle (it parks or breaks down), robots already queued behind it
  are re-evaluated. Without this, the Phase 24 benchmark found robots waiting forever.

Without a floor plan (`travel_time_min` scenarios), `TimedMotion` models each trip as one timed event.
