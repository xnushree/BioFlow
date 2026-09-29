"""Engine + bus working together: a hand-written plate workflow.

This is a preview of the pattern equipment will use from Phase 5: scheduled
events drive time forward, and bus notifications announce state changes to
listeners that the equipment does not know about.
"""

from bioflow.core.events import ALL_EVENTS, Event
from bioflow.core.simulation import SimulationEngine
from bioflow.domain import PlateState

INCUBATION_MIN = 720.0
MEDIA_EXCHANGE_MIN = 15.0


def test_plate_workflow_drives_notifications_in_time_order() -> None:
    engine = SimulationEngine()
    plate_state = {"EXP001-P001": PlateState.CREATED}
    telemetry: list[tuple[float, str]] = []
    engine.bus.subscribe(ALL_EVENTS, lambda e: telemetry.append((e.timestamp, e.event_type)))

    def start_incubation(event: Event) -> None:
        plate_state[event.payload["plate_id"]] = PlateState.INCUBATING
        engine.publish("INCUBATION_STARTED", "INC_01", payload=event.payload)
        engine.schedule(INCUBATION_MIN, "INCUBATION_DONE", "INC_01", start_media_exchange,
                        payload=event.payload)

    def start_media_exchange(event: Event) -> None:
        plate_state[event.payload["plate_id"]] = PlateState.PROCESSING
        engine.publish("MEDIA_EXCHANGE_STARTED", "MEDIA_01", payload=event.payload)
        engine.schedule(MEDIA_EXCHANGE_MIN, "MEDIA_EXCHANGE_DONE", "MEDIA_01", finish,
                        payload=event.payload)

    def finish(event: Event) -> None:
        plate_state[event.payload["plate_id"]] = PlateState.WAITING
        engine.publish("PLATE_READY", "MEDIA_01", payload=event.payload)

    engine.schedule(0, "PLATE_ARRIVED", "ROBOT_01", start_incubation,
                    payload={"plate_id": "EXP001-P001"})
    engine.run()

    assert plate_state["EXP001-P001"] is PlateState.WAITING
    assert engine.now == INCUBATION_MIN + MEDIA_EXCHANGE_MIN
    # Only bus notifications reach telemetry; scheduled events are internal mechanics.
    assert telemetry == [
        (0.0, "INCUBATION_STARTED"),
        (720.0, "MEDIA_EXCHANGE_STARTED"),
        (735.0, "PLATE_READY"),
    ]
