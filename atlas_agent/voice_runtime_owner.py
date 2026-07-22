from __future__ import annotations

import threading
from collections.abc import Callable

from atlas_agent.runtime_factory import RuntimeBundle
from atlas_agent.voice_controller import (
    AgentVoiceController,
    AgentVoiceResponse,
)


class VoiceRuntimeOwner:
    """Lazily owns one shared voice/phone agent runtime."""

    def __init__(
        self,
        bundle_factory: Callable[[], RuntimeBundle],
        *,
        controller_factory: Callable[
            [RuntimeBundle],
            AgentVoiceController,
        ] = AgentVoiceController,
    ) -> None:
        if not callable(bundle_factory):
            raise TypeError(
                "bundle_factory must be callable"
            )

        if not callable(controller_factory):
            raise TypeError(
                "controller_factory must be callable"
            )

        self._bundle_factory = bundle_factory
        self._controller_factory = (
            controller_factory
        )
        self._controller: (
            AgentVoiceController | None
        ) = None
        self._closed = False
        self._lock = threading.RLock()

    @property
    def initialized(self) -> bool:
        with self._lock:
            return self._controller is not None

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def handle_goal(
        self,
        goal: str,
        *,
        source: str = "voice",
    ) -> AgentVoiceResponse:
        # Keep the owner lock for the whole request. This
        # prevents shutdown from closing the shared bundle
        # while a voice or phone mission is still running.
        with self._lock:
            controller = (
                self._get_controller_locked()
            )
            return controller.handle_goal(
                goal,
                source=source,
            )

    def confirm_pending(
        self, *, confirm: bool
    ) -> AgentVoiceResponse:
        with self._lock:
            controller = (
                self._get_controller_locked()
            )
            return controller.confirm_pending(
                confirm=confirm
            )

    def resolve_pending(self, *, action: str) -> AgentVoiceResponse:
        with self._lock:
            controller = self._get_controller_locked()
            return controller.resolve_pending(action=action)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return

            self._closed = True
            controller = self._controller
            self._controller = None

            if controller is not None:
                controller.close()

    def _get_controller_locked(
        self,
    ) -> AgentVoiceController:
        if self._closed:
            raise RuntimeError(
                "voice runtime owner is closed"
            )

        if self._controller is None:
            bundle = self._bundle_factory()
            self._controller = (
                self._controller_factory(bundle)
            )

        return self._controller
