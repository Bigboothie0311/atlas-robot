from threading import Event
from typing import Any

import pytest

from atlas_agent.results import ResultStatus, ToolResult
from atlas_agent.tasks import ToolCall
from atlas_agent.verifier import (
    ResultVerifier,
    VerificationCheck,
    VerificationStatus,
)


def make_call() -> ToolCall:
    return ToolCall(
        tool_name="test.tool",
        task_id="task-123",
    )


def make_result(
    call: ToolCall,
    **overrides: Any,
) -> ToolResult:
    values: dict[str, Any] = {
        "tool_name": call.tool_name,
        "call_id": call.call_id,
        "task_id": call.task_id,
        "status": ResultStatus.SUCCESS,
        "output": "done",
    }
    values.update(overrides)
    return ToolResult(**values)


def test_configured_verifier_confirms_result() -> None:
    call = make_call()
    result = make_result(call)
    verifier = ResultVerifier()
    verifier.register(
        "test.tool",
        lambda call, result: VerificationCheck(
            verified=result.output == "done",
            reason="Expected output was returned.",
            evidence={"output": result.output},
        ),
    )

    verification = verifier.verify(call, result)

    assert verification.verified is True
    assert verification.status is VerificationStatus.VERIFIED
    assert verification.evidence == {"output": "done"}


def test_configured_verifier_can_reject_result() -> None:
    call = make_call()
    result = make_result(call)
    verifier = ResultVerifier()
    verifier.register(
        "test.tool",
        lambda call, result: VerificationCheck(
            verified=False,
            reason="Expected file was not found.",
        ),
    )

    verification = verifier.verify(call, result)

    assert verification.verified is False
    assert verification.status is VerificationStatus.FAILED
    assert verification.reason == "Expected file was not found."


def test_missing_verifier_is_not_treated_as_verified() -> None:
    call = make_call()
    verification = ResultVerifier().verify(
        call,
        make_result(call),
    )

    assert verification.verified is False
    assert verification.status is VerificationStatus.NOT_CONFIGURED


def test_failed_execution_skips_verifier_handler() -> None:
    called = Event()
    call = make_call()
    verifier = ResultVerifier()

    def handler(
        call: ToolCall,
        result: ToolResult,
    ) -> VerificationCheck:
        called.set()
        return VerificationCheck(True, "Verified.")

    verifier.register("test.tool", handler)
    result = make_result(
        call,
        status=ResultStatus.ERROR,
        error="Tool failed.",
    )

    verification = verifier.verify(call, result)

    assert verification.status is VerificationStatus.FAILED
    assert called.is_set() is False
    assert verification.evidence["result_status"] == "error"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"tool_name": "other.tool"}, "Tool name"),
        ({"call_id": "other-call"}, "Call ID"),
        ({"task_id": "other-task"}, "Task ID"),
    ],
)
def test_mismatched_result_identity_is_rejected(
    overrides: dict[str, Any],
    reason: str,
) -> None:
    call = make_call()
    verification = ResultVerifier().verify(
        call,
        make_result(call, **overrides),
    )

    assert verification.status is VerificationStatus.FAILED
    assert reason in verification.reason


def test_verifier_exception_becomes_error() -> None:
    call = make_call()
    verifier = ResultVerifier()

    def broken_verifier(
        call: ToolCall,
        result: ToolResult,
    ) -> VerificationCheck:
        raise RuntimeError("verification broke")

    verifier.register("test.tool", broken_verifier)
    verification = verifier.verify(call, make_result(call))

    assert verification.status is VerificationStatus.ERROR
    assert "RuntimeError: verification broke" in verification.reason


def test_invalid_verifier_response_becomes_error() -> None:
    call = make_call()
    verifier = ResultVerifier()
    verifier.register(
        "test.tool",
        lambda call, result: True,
    )

    verification = verifier.verify(call, make_result(call))

    assert verification.status is VerificationStatus.ERROR
    assert "invalid response" in verification.reason


def test_verifier_registration_is_protected() -> None:
    verifier = ResultVerifier()

    def handler(
        call: ToolCall,
        result: ToolResult,
    ) -> VerificationCheck:
        return VerificationCheck(True, "Verified.")

    verifier.register("test.tool", handler)

    with pytest.raises(ValueError, match="already registered"):
        verifier.register("test.tool", handler)

    assert verifier.unregister("test.tool") is handler

    with pytest.raises(KeyError, match="Unknown verifier"):
        verifier.unregister("test.tool")


def test_empty_verification_reason_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="verification reason cannot be empty",
    ):
        VerificationCheck(
            verified=False,
            reason="   ",
        )
