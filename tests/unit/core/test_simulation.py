"""Tests for the discrete-event simulation engine."""

import math

import pytest

from bioflow.core.events import Event
from bioflow.core.exceptions import SimulationError
from bioflow.core.simulation import SimulationEngine


class Recorder:
    """Handler that records (time, event_type) for every event it handles."""

    def __init__(self, engine: SimulationEngine) -> None:
        self.engine = engine
        self.log: list[tuple[float, str]] = []

    def __call__(self, event: Event) -> None:
        self.log.append((self.engine.now, event.event_type))


def test_events_run_in_timestamp_order_not_scheduling_order() -> None:
    engine = SimulationEngine()
    rec = Recorder(engine)
    engine.schedule(30, "C", "TEST", rec)
    engine.schedule(10, "A", "TEST", rec)
    engine.schedule(20, "B", "TEST", rec)

    engine.run()

    assert rec.log == [(10, "A"), (20, "B"), (30, "C")]


def test_simultaneous_events_run_in_scheduling_order() -> None:
    engine = SimulationEngine()
    rec = Recorder(engine)
    for name in ("first", "second", "third"):
        engine.schedule(5, name, "TEST", rec)

    engine.run()

    assert [name for _, name in rec.log] == ["first", "second", "third"]


def test_clock_jumps_to_each_event_timestamp() -> None:
    engine = SimulationEngine()
    engine.schedule(1440, "DAY_LATER", "TEST", lambda e: None)

    engine.step()

    assert engine.now == 1440


def test_handlers_can_schedule_follow_up_events() -> None:
    engine = SimulationEngine()
    rec = Recorder(engine)

    def start(event: Event) -> None:
        rec(event)
        engine.schedule(720, "INCUBATION_COMPLETE", "INC_01", rec)

    engine.schedule(0, "INCUBATION_START", "INC_01", start)
    processed = engine.run()

    assert processed == 2
    assert rec.log == [(0, "INCUBATION_START"), (720, "INCUBATION_COMPLETE")]


def test_scheduled_event_carries_all_fields() -> None:
    engine = SimulationEngine()

    event = engine.schedule(
        152.42, "PLATE_PICKUP_COMPLETE", "ROBOT_02", lambda e: None,
        target="MEDIA_STATION_01", payload={"plate_id": "P017"},
    )

    assert event.timestamp == 152.42
    assert event.source == "ROBOT_02"
    assert event.target == "MEDIA_STATION_01"
    assert event.payload == {"plate_id": "P017"}
    assert event.event_id == "EV00000000"


@pytest.mark.parametrize("delay", [-1.0, math.nan, math.inf])
def test_invalid_delays_are_rejected(delay: float) -> None:
    with pytest.raises(SimulationError):
        SimulationEngine().schedule(delay, "X", "TEST", lambda e: None)


def test_scheduling_in_the_past_is_rejected() -> None:
    engine = SimulationEngine(start_time=100)

    with pytest.raises(SimulationError, match="before now"):
        engine.schedule_at(99, "X", "TEST", lambda e: None)


def test_run_until_stops_at_horizon_and_keeps_later_events() -> None:
    engine = SimulationEngine()
    rec = Recorder(engine)
    engine.schedule(10, "A", "TEST", rec)
    engine.schedule(50, "B", "TEST", rec)  # exactly at the horizon: runs
    engine.schedule(90, "C", "TEST", rec)

    engine.run(until=50)

    assert [name for _, name in rec.log] == ["A", "B"]
    assert engine.now == 50
    assert engine.pending_count == 1


def test_run_until_advances_clock_even_with_empty_queue() -> None:
    engine = SimulationEngine()

    engine.run(until=300)

    assert engine.now == 300


def test_run_until_in_the_past_is_rejected() -> None:
    engine = SimulationEngine(start_time=10)

    with pytest.raises(SimulationError):
        engine.run(until=5)


def test_cancelled_events_never_run() -> None:
    engine = SimulationEngine()
    rec = Recorder(engine)
    move_done = engine.schedule(10, "MOVE_COMPLETE", "ROBOT_01", rec)
    engine.schedule(20, "OTHER", "TEST", rec)

    assert engine.cancel(move_done.event_id) is True
    assert engine.pending_count == 1
    engine.run()

    assert rec.log == [(20, "OTHER")]


def test_cancel_returns_false_for_unknown_or_already_run_events() -> None:
    engine = SimulationEngine()
    event = engine.schedule(1, "X", "TEST", lambda e: None)
    engine.run()

    assert engine.cancel(event.event_id) is False
    assert engine.cancel("EV99999999") is False


def test_peek_time_skips_cancelled_events() -> None:
    engine = SimulationEngine()
    first = engine.schedule(5, "A", "TEST", lambda e: None)
    engine.schedule(8, "B", "TEST", lambda e: None)
    engine.cancel(first.event_id)

    assert engine.peek_time() == 8


def test_max_events_guards_against_runaway_loops() -> None:
    engine = SimulationEngine()

    def forever(event: Event) -> None:
        engine.schedule(1, "TICK", "TEST", forever)

    engine.schedule(0, "TICK", "TEST", forever)

    assert engine.run(max_events=100) == 100
    assert engine.now == 99


def test_stop_from_inside_a_handler_ends_the_run() -> None:
    engine = SimulationEngine()
    rec = Recorder(engine)

    def emergency_stop(event: Event) -> None:
        rec(event)
        engine.stop()

    engine.schedule(1, "A", "TEST", rec)
    engine.schedule(2, "E_STOP", "TEST", emergency_stop)
    engine.schedule(3, "C", "TEST", rec)

    engine.run(until=100)

    assert [name for _, name in rec.log] == ["A", "E_STOP"]
    assert engine.now == 2  # stop leaves the clock where it halted
    assert engine.pending_count == 1


def test_step_on_empty_queue_returns_none() -> None:
    assert SimulationEngine().step() is None


def test_publish_delivers_immediately_at_current_time() -> None:
    engine = SimulationEngine(start_time=42)
    received: list[Event] = []
    engine.bus.subscribe("PLATE_PICKED", received.append)

    event = engine.publish("PLATE_PICKED", "ROBOT_01", payload={"plate_id": "P1"})

    assert received == [event]
    assert event.timestamp == 42


def test_event_ids_are_unique_across_schedule_and_publish() -> None:
    engine = SimulationEngine()
    ids = {
        engine.schedule(1, "A", "TEST", lambda e: None).event_id,
        engine.publish("B", "TEST").event_id,
        engine.schedule(2, "C", "TEST", lambda e: None).event_id,
    }

    assert len(ids) == 3


def test_same_seed_gives_same_random_sequence() -> None:
    first, second = SimulationEngine(seed=7), SimulationEngine(seed=7)

    assert [first.rng.random() for _ in range(5)] == [second.rng.random() for _ in range(5)]
    assert SimulationEngine(seed=8).rng.random() != SimulationEngine(seed=7).rng.random()


def test_events_processed_counter() -> None:
    engine = SimulationEngine()
    for delay in range(3):
        engine.schedule(delay, "X", "TEST", lambda e: None)

    engine.run()

    assert engine.events_processed == 3
    assert engine.pending_count == 0
