from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from atlas_agent.events import AtlasEvent


EventHandler = Callable[[AtlasEvent], Any]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        if handler not in self._handlers[event_name]:
            self._handlers[event_name].append(handler)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event_name, [])
        if handler in handlers:
            handlers.remove(handler)

    def publish(self, event: AtlasEvent) -> list[Any]:
        results: list[Any] = []

        for handler in self._handlers.get(event.name, []):
            results.append(handler(event))

        for handler in self._handlers.get("*", []):
            results.append(handler(event))

        return results
