from threading import Event
from typing import Any

import pytest

from atlas_agent.pc_client import PCClient


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def unused_call(
    action: str,
    arguments: dict[str, Any] | None,
    timeout: float,
) -> tuple[bool, Any]:
    return True, {"ok": True}


def test_execute_returns_successful_structured_result() -> None:
    received: dict[str, Any] = {}

    def call(
        action: str,
        arguments: dict[str, Any] | None,
        timeout: float,
    ) -> tuple[bool, Any]:
        received["action"] = action
        received["arguments"] = arguments
        received["timeout"] = timeout
        return True, {"windows": ["Fusion 360"]}

    client = PCClient(
        call_handler=call,
        configured_handler=lambda: True,
        reachable_handler=lambda: True,
        wake_handler=lambda: "Wake sent.",
    )

    result = client.execute(
        "active_apps",
        {"limit": 5},
        timeout_seconds=12,
    )

    assert result.ok is True
    assert result.error is None
    assert result.data == {"windows": ["Fusion 360"]}
    assert received == {
        "action": "active_apps",
        "arguments": {"limit": 5},
        "timeout": 12,
    }


def test_execute_returns_companion_error() -> None:
    client = PCClient(
        call_handler=lambda action, arguments, timeout: (
            False,
            "unknown action",
        ),
        configured_handler=lambda: True,
        reachable_handler=lambda: True,
        wake_handler=lambda: "Wake sent.",
    )

    result = client.execute("missing_action")

    assert result.ok is False
    assert result.data is None
    assert result.error == "unknown action"


def test_execute_catches_transport_exception() -> None:
    def broken_call(
        action: str,
        arguments: dict[str, Any] | None,
        timeout: float,
    ) -> tuple[bool, Any]:
        raise RuntimeError("connection broke")

    client = PCClient(
        call_handler=broken_call,
        configured_handler=lambda: True,
        reachable_handler=lambda: True,
        wake_handler=lambda: "Wake sent.",
    )

    result = client.execute("active_apps")

    assert result.ok is False
    assert result.error == "RuntimeError: connection broke"


def test_unconfigured_companion_does_not_attempt_wake() -> None:
    reachable_called = Event()
    wake_called = Event()

    def reachable() -> bool:
        reachable_called.set()
        return False

    def wake() -> str:
        wake_called.set()
        return "Wake sent."

    client = PCClient(
        call_handler=unused_call,
        configured_handler=lambda: False,
        reachable_handler=reachable,
        wake_handler=wake,
    )

    result = client.ensure_online()

    assert result.configured is False
    assert result.online is False
    assert result.wake_attempted is False
    assert result.attempts == 0
    assert reachable_called.is_set() is False
    assert wake_called.is_set() is False


def test_reachable_companion_does_not_attempt_wake() -> None:
    wake_called = Event()

    def wake() -> str:
        wake_called.set()
        return "Wake sent."

    client = PCClient(
        call_handler=unused_call,
        configured_handler=lambda: True,
        reachable_handler=lambda: True,
        wake_handler=wake,
    )

    result = client.ensure_online()

    assert result.success is True
    assert result.wake_attempted is False
    assert result.attempts == 1
    assert wake_called.is_set() is False


def test_offline_pc_is_woken_and_verified() -> None:
    clock = FakeClock()
    reachability = iter([False, False, True])
    wake_called = Event()

    def wake() -> str:
        wake_called.set()
        return "Wake signal sent."

    client = PCClient(
        call_handler=unused_call,
        configured_handler=lambda: True,
        reachable_handler=lambda: next(reachability),
        wake_handler=wake,
        sleep_handler=clock.sleep,
        clock_handler=clock,
    )

    result = client.ensure_online(
        timeout_seconds=20,
        poll_interval_seconds=5,
    )

    assert result.success is True
    assert result.wake_attempted is True
    assert result.attempts == 3
    assert result.wake_response == "Wake signal sent."
    assert result.duration_ms == 10000
    assert wake_called.is_set() is True


def test_wake_timeout_returns_honest_failure() -> None:
    clock = FakeClock()

    client = PCClient(
        call_handler=unused_call,
        configured_handler=lambda: True,
        reachable_handler=lambda: False,
        wake_handler=lambda: "Wake signal sent.",
        sleep_handler=clock.sleep,
        clock_handler=clock,
    )

    result = client.ensure_online(
        timeout_seconds=12,
        poll_interval_seconds=5,
    )

    assert result.success is False
    assert result.wake_attempted is True
    assert result.attempts == 4
    assert result.duration_ms == 12000
    assert "did not become reachable" in result.message


def test_offline_check_can_skip_wake() -> None:
    wake_called = Event()

    def wake() -> str:
        wake_called.set()
        return "Wake sent."

    client = PCClient(
        call_handler=unused_call,
        configured_handler=lambda: True,
        reachable_handler=lambda: False,
        wake_handler=wake,
    )

    result = client.ensure_online(wake_if_needed=False)

    assert result.online is False
    assert result.wake_attempted is False
    assert result.attempts == 1
    assert wake_called.is_set() is False


def test_reachability_exception_is_reported() -> None:
    def broken_reachability() -> bool:
        raise OSError("network check failed")

    client = PCClient(
        call_handler=unused_call,
        configured_handler=lambda: True,
        reachable_handler=broken_reachability,
        wake_handler=lambda: "Wake sent.",
    )

    result = client.ensure_online(wake_if_needed=False)

    assert result.online is False
    assert result.error == "OSError: network check failed"


def test_invalid_arguments_are_rejected() -> None:
    client = PCClient(
        call_handler=unused_call,
        configured_handler=lambda: True,
        reachable_handler=lambda: True,
        wake_handler=lambda: "Wake sent.",
    )

    with pytest.raises(ValueError, match="action cannot be empty"):
        client.execute("   ")

    with pytest.raises(ValueError, match="greater than zero"):
        client.execute("active_apps", timeout_seconds=0)

    with pytest.raises(ValueError, match="greater than zero"):
        client.ensure_online(timeout_seconds=0)

    with pytest.raises(ValueError, match="greater than zero"):
        client.ensure_online(poll_interval_seconds=0)
