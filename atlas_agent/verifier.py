from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from atlas_agent.results import ToolResult
from atlas_agent.tasks import ToolCall


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    verified: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("verification reason cannot be empty")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    tool_name: str
    call_id: str
    status: VerificationStatus
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.status is VerificationStatus.VERIFIED


VerificationHandler = Callable[
    [ToolCall, ToolResult],
    VerificationCheck,
]


class ResultVerifier:
    def __init__(self) -> None:
        self._handlers: dict[str, VerificationHandler] = {}

    def register(
        self,
        tool_name: str,
        handler: VerificationHandler,
    ) -> None:
        if not tool_name.strip():
            raise ValueError("tool_name cannot be empty")

        if tool_name in self._handlers:
            raise ValueError(
                f"Verifier already registered: {tool_name}"
            )

        self._handlers[tool_name] = handler

    def unregister(self, tool_name: str) -> VerificationHandler:
        try:
            return self._handlers.pop(tool_name)
        except KeyError:
            raise KeyError(
                f"Unknown verifier: {tool_name}"
            ) from None

    def verify(
        self,
        call: ToolCall,
        result: ToolResult,
    ) -> VerificationResult:
        if call.tool_name != result.tool_name:
            return VerificationResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                status=VerificationStatus.FAILED,
                reason="Tool name does not match the execution result.",
                evidence={
                    "call_tool_name": call.tool_name,
                    "result_tool_name": result.tool_name,
                },
            )

        if call.call_id != result.call_id:
            return VerificationResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                status=VerificationStatus.FAILED,
                reason="Call ID does not match the execution result.",
                evidence={
                    "call_id": call.call_id,
                    "result_call_id": result.call_id,
                },
            )

        if call.task_id != result.task_id:
            return VerificationResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                status=VerificationStatus.FAILED,
                reason="Task ID does not match the execution result.",
                evidence={
                    "call_task_id": call.task_id,
                    "result_task_id": result.task_id,
                },
            )

        if not result.success:
            return VerificationResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                status=VerificationStatus.FAILED,
                reason="Tool execution did not succeed.",
                evidence={
                    "result_status": result.status.value,
                    "result_error": result.error,
                },
            )

        handler = self._handlers.get(call.tool_name)

        if handler is None:
            return VerificationResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                status=VerificationStatus.NOT_CONFIGURED,
                reason="No verifier is configured for this tool.",
            )

        try:
            check = handler(call, result)
        except Exception as exc:
            return VerificationResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                status=VerificationStatus.ERROR,
                reason=f"Verifier failed: {type(exc).__name__}: {exc}",
            )

        if not isinstance(check, VerificationCheck):
            return VerificationResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                status=VerificationStatus.ERROR,
                reason="Verifier returned an invalid response.",
            )

        status = (
            VerificationStatus.VERIFIED
            if check.verified
            else VerificationStatus.FAILED
        )

        return VerificationResult(
            tool_name=call.tool_name,
            call_id=call.call_id,
            status=status,
            reason=check.reason,
            evidence=check.evidence,
        )
