"""Tests for the publish/subscribe event bus."""

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
