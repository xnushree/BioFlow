"""Tests for the publish/subscribe event bus."""

import pytest

from bioflow.core.event_bus import EventBus
from bioflow.core.events import ALL_EVENTS, Event


def make_event(event_type: str = "PLATE_PICKED") -> Event:
    return Event("EV1", 0.0, event_type, "ROBOT_01")


def test_subscriber_receives_matching_events_only() -> None:
    bus = EventBus()
    received: list[str] = []
    bus.subscribe("PLATE_PICKED", lambda e: received.append(e.event_type))

    bus.publish(make_event("PLATE_PICKED"))
    bus.publish(make_event("PLATE_PLACED"))

    assert received == ["PLATE_PICKED"]


def test_subscribers_run_in_subscription_order_then_wildcards() -> None:
    bus = EventBus()
    calls: list[str] = []
    bus.subscribe(ALL_EVENTS, lambda e: calls.append("telemetry"))
    bus.subscribe("PLATE_PICKED", lambda e: calls.append("scheduler"))
    bus.subscribe("PLATE_PICKED", lambda e: calls.append("dashboard"))

    bus.publish(make_event())

    assert calls == ["scheduler", "dashboard", "telemetry"]


def test_wildcard_receives_every_event_type() -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(ALL_EVENTS, lambda e: seen.append(e.event_type))

    bus.publish(make_event("A"))
    bus.publish(make_event("B"))

    assert seen == ["A", "B"]


def test_unsubscribe_stops_delivery_and_is_idempotent() -> None:
    bus = EventBus()
    received: list[Event] = []
    unsubscribe = bus.subscribe("PLATE_PICKED", received.append)

    unsubscribe()
    unsubscribe()
    bus.publish(make_event())

    assert received == []
    assert bus.subscriber_count("PLATE_PICKED") == 0


def test_publish_without_subscribers_is_a_no_op() -> None:
    EventBus().publish(make_event())


def test_handler_subscribing_during_delivery_only_sees_later_events() -> None:
    bus = EventBus()
    late: list[Event] = []

    def subscribe_another(event: Event) -> None:
        bus.subscribe("PLATE_PICKED", late.append)

    bus.subscribe("PLATE_PICKED", subscribe_another)
    bus.publish(make_event())
    assert late == []

    bus.publish(make_event())
    assert len(late) == 1


# ------------------------------------------------ Phase 18: ordering, isolation
def test_every_subscriber_sees_events_in_publish_order() -> None:
    """The Phase 16 bug pattern: a handler reacts to COMPLETED by publishing STARTED.

    With nested delivery a later subscriber saw STARTED before COMPLETED. With causal
    FIFO delivery, every subscriber sees COMPLETED first.
    """
    bus = EventBus()
    early: list[str] = []
    late: list[str] = []
    bus.subscribe("COMPLETED", lambda e: bus.publish(make_event("STARTED")))  # e.g. the dispatcher
    bus.subscribe(ALL_EVENTS, lambda e: early.append(e.event_type))
    bus.subscribe(ALL_EVENTS, lambda e: late.append(e.event_type))  # e.g. the fault detector

    bus.publish(make_event("COMPLETED"))

    assert early == late == ["COMPLETED", "STARTED"]


def test_follow_up_events_are_delivered_before_publish_returns() -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe("A", lambda e: bus.publish(make_event("B")))
    bus.subscribe("B", lambda e: seen.append("B"))

    bus.publish(make_event("A"))

    assert seen == ["B"]  # synchronous completion from the caller's point of view


def test_failing_observer_is_isolated_and_counted() -> None:
    bus = EventBus()
    received: list[str] = []

    def broken_dashboard(event: Event) -> None:
        raise RuntimeError("chart library crashed")

    bus.subscribe(ALL_EVENTS, broken_dashboard, critical=False, name="dashboard")
    bus.subscribe(ALL_EVENTS, lambda e: received.append(e.event_type))

    bus.publish(make_event("A"))
    bus.publish(make_event("B"))

    assert received == ["A", "B"]
    assert bus.stats.handler_errors == {"dashboard": 2}


def test_failing_critical_subscriber_propagates_and_clears_the_queue() -> None:
    bus = EventBus()
    delivered: list[str] = []

    def dispatcher_bug(event: Event) -> None:
        bus.publish(make_event("FOLLOW_UP"))
        raise ValueError("control logic bug")

    bus.subscribe("A", dispatcher_bug)
    bus.subscribe(ALL_EVENTS, lambda e: delivered.append(e.event_type))

    with pytest.raises(ValueError):
        bus.publish(make_event("A"))
    bus.publish(make_event("C"))

    assert delivered == ["C"]  # the aborted cascade is not replayed later


def test_filtered_subscription() -> None:
    bus = EventBus()
    robot_two: list[str] = []
    bus.subscribe(ALL_EVENTS, lambda e: robot_two.append(e.source), where=lambda e: e.source == "ROBOT_02")

    bus.publish(Event("EV1", 0.0, "MOVED", "ROBOT_01"))
    bus.publish(Event("EV2", 0.0, "MOVED", "ROBOT_02"))

    assert robot_two == ["ROBOT_02"]


def test_bus_statistics() -> None:
    bus = EventBus()
    bus.subscribe("A", lambda e: [bus.publish(make_event("B")) for _ in range(3)])

    bus.publish(make_event("A"))

    stats = bus.stats
    assert stats.published == {"A": 1, "B": 3}
    assert stats.total_published == 4
    assert stats.max_queue_depth == 3
