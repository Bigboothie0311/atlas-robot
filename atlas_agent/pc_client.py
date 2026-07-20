from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Any

from atlas_agent.tasks import utc_now


CompanionCall = Callable[
    [str, dict[str, Any] | None, float],
    tuple[bool, Any],
]
BooleanCheck = Callable[[], bool]
WakeHandler = Callable[[], str]
SleepHandler = Callable[[float], None]
ClockHandler = Callable[[], float]


@dataclass(frozen=True, slots=True)
class PCActionResult:
    action: str
    ok: bool
    data: Any
    error: str | None
    started_at: str
    finished_at: str
    duration_ms: float


@dataclass(frozen=True, slots=True)
class PCConnectionResult:
    configured: bool
    online: bool
    wake_attempted: bool
    attempts: int
    message: str
    wake_response: str | None
    error: str | None
    duration_ms: float

    @property
    def success(self) -> bool:
        return self.configured and self.online


class PCClient:
    def __init__(
        self,
        call_handler: CompanionCall | None = None,
        configured_handler: BooleanCheck | None = None,
        reachable_handler: BooleanCheck | None = None,
        wake_handler: WakeHandler | None = None,
        *,
        sleep_handler: SleepHandler = sleep,
        clock_handler: ClockHandler = monotonic,
    ) -> None:
        if (
            call_handler is None
            or configured_handler is None
            or reachable_handler is None
        ):
            import pc_control

            call_handler = call_handler or pc_control._call
            configured_handler = (
                configured_handler
                or pc_control.is_configured
            )
            reachable_handler = (
                reachable_handler
                or pc_control.pc_reachable
            )

        if wake_handler is None:
            import pc_power

            wake_handler = pc_power.send_wake_packet

        self._call_handler = call_handler
        self._configured_handler = configured_handler
        self._reachable_handler = reachable_handler
        self._wake_handler = wake_handler
        self._sleep = sleep_handler
        self._clock = clock_handler

    def is_configured(self) -> bool:
        try:
            return bool(self._configured_handler())
        except Exception:
            return False

    def is_reachable(self) -> bool:
        online, _error = self._check_reachable()
        return online

    def ensure_online(
        self,
        *,
        wake_if_needed: bool = True,
        timeout_seconds: float = 90,
        poll_interval_seconds: float = 5,
    ) -> PCConnectionResult:
        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be greater than zero"
            )

        if poll_interval_seconds <= 0:
            raise ValueError(
                "poll_interval_seconds must be greater than zero"
            )

        started_clock = self._clock()

        if not self.is_configured():
            return self._connection_result(
                started_clock,
                configured=False,
                online=False,
                wake_attempted=False,
                attempts=0,
                message="The PC companion is not configured.",
            )

        online, reachability_error = self._check_reachable()

        if online:
            return self._connection_result(
                started_clock,
                configured=True,
                online=True,
                wake_attempted=False,
                attempts=1,
                message="The PC companion is reachable.",
            )

        if not wake_if_needed:
            return self._connection_result(
                started_clock,
                configured=True,
                online=False,
                wake_attempted=False,
                attempts=1,
                message="The PC companion is offline.",
                error=reachability_error,
            )

        try:
            wake_response = str(self._wake_handler())
        except Exception as exc:
            return self._connection_result(
                started_clock,
                configured=True,
                online=False,
                wake_attempted=True,
                attempts=1,
                message="The PC wake request failed.",
                error=f"{type(exc).__name__}: {exc}",
            )

        deadline = self._clock() + timeout_seconds
        attempts = 1
        last_error = reachability_error

        while True:
            remaining = deadline - self._clock()

            if remaining <= 0:
                break

            self._sleep(
                min(poll_interval_seconds, remaining)
            )
            attempts += 1
            online, last_error = self._check_reachable()

            if online:
                return self._connection_result(
                    started_clock,
                    configured=True,
                    online=True,
                    wake_attempted=True,
                    attempts=attempts,
                    message=(
                        "The PC companion came online "
                        "after the wake request."
                    ),
                    wake_response=wake_response,
                )

        return self._connection_result(
            started_clock,
            configured=True,
            online=False,
            wake_attempted=True,
            attempts=attempts,
            message=(
                "The PC companion did not become reachable "
                "before the timeout."
            ),
            wake_response=wake_response,
            error=last_error,
        )

    def execute(
        self,
        action: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 25,
    ) -> PCActionResult:
        if not action.strip():
            raise ValueError("action cannot be empty")

        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be greater than zero"
            )

        started_at = utc_now()
        started_clock = self._clock()

        try:
            ok, payload = self._call_handler(
                action,
                dict(arguments or {}),
                timeout_seconds,
            )
        except Exception as exc:
            return self._action_result(
                action,
                started_at,
                started_clock,
                ok=False,
                data=None,
                error=f"{type(exc).__name__}: {exc}",
            )

        if ok:
            return self._action_result(
                action,
                started_at,
                started_clock,
                ok=True,
                data=payload,
                error=None,
            )

        return self._action_result(
            action,
            started_at,
            started_clock,
            ok=False,
            data=None,
            error=str(payload),
        )

    def _check_reachable(
        self,
    ) -> tuple[bool, str | None]:
        try:
            return bool(self._reachable_handler()), None
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _connection_result(
        self,
        started_clock: float,
        *,
        configured: bool,
        online: bool,
        wake_attempted: bool,
        attempts: int,
        message: str,
        wake_response: str | None = None,
        error: str | None = None,
    ) -> PCConnectionResult:
        return PCConnectionResult(
            configured=configured,
            online=online,
            wake_attempted=wake_attempted,
            attempts=attempts,
            message=message,
            wake_response=wake_response,
            error=error,
            duration_ms=round(
                (self._clock() - started_clock) * 1000,
                3,
            ),
        )

    def _action_result(
        self,
        action: str,
        started_at: str,
        started_clock: float,
        *,
        ok: bool,
        data: Any,
        error: str | None,
    ) -> PCActionResult:
        return PCActionResult(
            action=action,
            ok=ok,
            data=data,
            error=error,
            started_at=started_at,
            finished_at=utc_now(),
            duration_ms=round(
                (self._clock() - started_clock) * 1000,
                3,
            ),
        )
