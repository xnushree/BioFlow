"""Faults: injection of hidden hardware problems, observation, detection and recovery.

The separation between these layers is the point of the design:

* ``fault_injector`` changes the *physical truth* (a heater dies, a robot freezes)
  and records it on a ground-truth channel used only for scoring.
* ``monitoring`` publishes what a real plant could observe: heartbeats and sensor readings.
* ``fault_detector`` (Phase 16) infers faults from those observations alone.
* ``recovery`` (Phase 17) reacts to detected faults.
"""
