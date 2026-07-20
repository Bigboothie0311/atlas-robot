from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum

from atlas_agent.tools import AtlasTool


class PermissionLevel(IntEnum):
    AUTONOMOUS = 0
    LOGGED_AUTONOMOUS = 1
    CONFIRMATION_REQUIRED = 2
    LOCKED = 3


class PermissionOutcome(str, Enum):
    ALLOW = "allow"
    ALLOW_LOGGED = "allow_logged"
    REQUIRE_CONFIRMATION = "require_confirmation"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    tool_name: str
    permission_level: int
    outcome: PermissionOutcome
    reason: str

    @property
    def allowed(self) -> bool:
        return self.outcome in {
            PermissionOutcome.ALLOW,
            PermissionOutcome.ALLOW_LOGGED,
        }

    @property
    def requires_confirmation(self) -> bool:
        return self.outcome is PermissionOutcome.REQUIRE_CONFIRMATION

    @property
    def log_required(self) -> bool:
        return self.outcome is not PermissionOutcome.ALLOW


class PermissionPolicy:
    def evaluate(
        self,
        tool: AtlasTool,
        *,
        confirmed: bool = False,
    ) -> PermissionDecision:
        level = tool.permission_level

        if level == PermissionLevel.AUTONOMOUS:
            return PermissionDecision(
                tool_name=tool.name,
                permission_level=level,
                outcome=PermissionOutcome.ALLOW,
                reason="Level 0 tool may run autonomously.",
            )

        if level == PermissionLevel.LOGGED_AUTONOMOUS:
            return PermissionDecision(
                tool_name=tool.name,
                permission_level=level,
                outcome=PermissionOutcome.ALLOW_LOGGED,
                reason="Level 1 tool may run autonomously with logging.",
            )

        if level == PermissionLevel.CONFIRMATION_REQUIRED:
            if confirmed:
                return PermissionDecision(
                    tool_name=tool.name,
                    permission_level=level,
                    outcome=PermissionOutcome.ALLOW_LOGGED,
                    reason="Level 2 tool was explicitly confirmed.",
                )

            return PermissionDecision(
                tool_name=tool.name,
                permission_level=level,
                outcome=PermissionOutcome.REQUIRE_CONFIRMATION,
                reason="Level 2 tool requires explicit confirmation.",
            )

        if level == PermissionLevel.LOCKED:
            return PermissionDecision(
                tool_name=tool.name,
                permission_level=level,
                outcome=PermissionOutcome.DENY,
                reason="Level 3 tool is locked by default.",
            )

        return PermissionDecision(
            tool_name=tool.name,
            permission_level=level,
            outcome=PermissionOutcome.DENY,
            reason=f"Invalid permission level: {level}",
        )
