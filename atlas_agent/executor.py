from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
)
from time import monotonic
from typing import Any

from atlas_agent.permissions import PermissionPolicy
from atlas_agent.results import ResultStatus, ToolResult
from atlas_agent.tasks import ToolCall, utc_now
from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import ToolHandler


def invoke_handler(
    handler: ToolHandler,
    arguments: dict[str, Any],
) -> tuple[bool, Any]:
    try:
        return True, handler(**arguments)
    except Exception as exc:
        return False, exc


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        permission_policy: PermissionPolicy | None = None,
        *,
        max_workers: int = 4,
        audit_sink: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")

        self._registry = registry
        self._permission_policy = permission_policy or PermissionPolicy()
        self.audit_sink = audit_sink
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="atlas-tool",
        )

    def execute(
        self,
        call: ToolCall,
        *,
        confirmed: bool = False,
    ) -> ToolResult:
        result = self._execute(call, confirmed=confirmed)
        self._audit(result)
        return result

    def _audit(self, result: ToolResult) -> None:
        """Persist an audit record for anything beyond a routine
        level-0 success: logged permission use, denials, confirmations,
        and failures. A broken sink never breaks execution."""
        if self.audit_sink is None:
            return

        routine = (
            result.status is ResultStatus.SUCCESS
            and result.metadata.get("permission_outcome") == "allow"
        )

        if routine:
            return

        try:
            self.audit_sink({
                "tool_name": result.tool_name,
                "call_id": result.call_id,
                "task_id": result.task_id,
                "status": result.status.value,
                "permission_level": result.metadata.get(
                    "permission_level"
                ),
                "permission_outcome": result.metadata.get(
                    "permission_outcome"
                ),
                "duration_ms": result.duration_ms,
                "error": result.error,
            })
        except Exception as error:
            print(
                "Tool audit sink failed:",
                type(error).__name__,
                error,
                flush=True,
            )

    def _execute(
        self,
        call: ToolCall,
        *,
        confirmed: bool = False,
    ) -> ToolResult:
        started_at = utc_now()
        started_clock = monotonic()

        try:
            tool = self._registry.get(call.tool_name)
        except KeyError:
            return self._result(
                call,
                ResultStatus.DENIED,
                started_at,
                started_clock,
                error=f"Unregistered tool: {call.tool_name}",
            )

        decision = self._permission_policy.evaluate(
            tool,
            confirmed=confirmed,
        )
        permission_metadata = {
            "permission_level": decision.permission_level,
            "permission_outcome": decision.outcome.value,
        }

        if decision.requires_confirmation:
            return self._result(
                call,
                ResultStatus.CONFIRMATION_REQUIRED,
                started_at,
                started_clock,
                error=decision.reason,
                metadata=permission_metadata,
            )

        if not decision.allowed:
            return self._result(
                call,
                ResultStatus.DENIED,
                started_at,
                started_clock,
                error=decision.reason,
                metadata=permission_metadata,
            )

        if tool.timeout_seconds <= 0:
            return self._result(
                call,
                ResultStatus.ERROR,
                started_at,
                started_clock,
                error="Tool timeout_seconds must be greater than zero.",
                metadata=permission_metadata,
            )

        try:
            future = self._pool.submit(
                invoke_handler,
                tool.handler,
                dict(call.arguments),
            )
        except RuntimeError as exc:
            return self._result(
                call,
                ResultStatus.ERROR,
                started_at,
                started_clock,
                error=f"Executor unavailable: {exc}",
                metadata=permission_metadata,
            )

        try:
            handler_succeeded, value = future.result(
                timeout=tool.timeout_seconds,
            )
        except FutureTimeoutError:
            cancelled = future.cancel()

            return self._result(
                call,
                ResultStatus.TIMEOUT,
                started_at,
                started_clock,
                error=(
                    f"Tool exceeded its {tool.timeout_seconds}-second timeout."
                ),
                metadata={
                    **permission_metadata,
                    "future_cancelled": cancelled,
                    "execution_may_continue": not cancelled,
                },
            )

        if not handler_succeeded:
            return self._result(
                call,
                ResultStatus.ERROR,
                started_at,
                started_clock,
                error=f"{type(value).__name__}: {value}",
                metadata=permission_metadata,
            )

        return self._result(
            call,
            ResultStatus.SUCCESS,
            started_at,
            started_clock,
            output=value,
            metadata=permission_metadata,
        )

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> ToolExecutor:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @staticmethod
    def _result(
        call: ToolCall,
        status: ResultStatus,
        started_at: str,
        started_clock: float,
        *,
        output: Any = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_name=call.tool_name,
            call_id=call.call_id,
            task_id=call.task_id,
            status=status,
            output=output,
            error=error,
            started_at=started_at,
            finished_at=utc_now(),
            duration_ms=round(
                (monotonic() - started_clock) * 1000,
                3,
            ),
            metadata=metadata or {},
        )
