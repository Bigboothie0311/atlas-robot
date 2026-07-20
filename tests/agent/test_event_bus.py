from atlas_agent.event_bus import EventBus
from atlas_agent.events import AtlasEvent


def test_publish_calls_matching_handler() -> None:
    bus = EventBus()
    received: list[AtlasEvent] = []

    bus.subscribe("test.event", received.append)

    event = AtlasEvent(
        name="test.event",
        source="pytest",
        data={"status": "ok"},
    )

    results = bus.publish(event)

    assert received == [event]
    assert results == [None]


def test_wildcard_handler_receives_all_events() -> None:
    bus = EventBus()
    received: list[str] = []

    bus.subscribe("*", lambda event: received.append(event.name))

    bus.publish(AtlasEvent(name="one", source="pytest"))
    bus.publish(AtlasEvent(name="two", source="pytest"))

    assert received == ["one", "two"]
