import pytest

from atlas_agent.permissions import (
    PermissionOutcome,
    PermissionPolicy,
)
from atlas_agent.tools import AtlasTool


def make_tool(permission_level: int) -> AtlasTool:
    return AtlasTool(
        name=f"test.level_{permission_level}",
        description="Permission-policy test tool.",
        runs_on="pi",
        handler=lambda: None,
        permission_level=permission_level,
    )


@pytest.mark.parametrize(
    ("level", "outcome", "log_required"),
    [
        (0, PermissionOutcome.ALLOW, False),
        (1, PermissionOutcome.ALLOW_LOGGED, True),
    ],
)
def test_autonomous_permission_levels(
    level: int,
    outcome: PermissionOutcome,
    log_required: bool,
) -> None:
    decision = PermissionPolicy().evaluate(make_tool(level))

    assert decision.allowed is True
    assert decision.outcome is outcome
    assert decision.requires_confirmation is False
    assert decision.log_required is log_required


def test_level_two_requires_confirmation() -> None:
    decision = PermissionPolicy().evaluate(make_tool(2))

    assert decision.allowed is False
    assert decision.requires_confirmation is True
    assert decision.outcome is PermissionOutcome.REQUIRE_CONFIRMATION
    assert decision.log_required is True


def test_confirmed_level_two_is_allowed_and_logged() -> None:
    decision = PermissionPolicy().evaluate(
        make_tool(2),
        confirmed=True,
    )

    assert decision.allowed is True
    assert decision.requires_confirmation is False
    assert decision.outcome is PermissionOutcome.ALLOW_LOGGED
    assert decision.log_required is True


def test_level_three_remains_locked_when_confirmed() -> None:
    decision = PermissionPolicy().evaluate(
        make_tool(3),
        confirmed=True,
    )

    assert decision.allowed is False
    assert decision.requires_confirmation is False
    assert decision.outcome is PermissionOutcome.DENY
    assert decision.log_required is True


@pytest.mark.parametrize("invalid_level", [-1, 4])
def test_invalid_permission_levels_are_denied(
    invalid_level: int,
) -> None:
    decision = PermissionPolicy().evaluate(
        make_tool(invalid_level),
        confirmed=True,
    )

    assert decision.allowed is False
    assert decision.outcome is PermissionOutcome.DENY
    assert decision.log_required is True
    assert "Invalid permission level" in decision.reason
