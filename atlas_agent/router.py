from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from atlas_agent.tasks import ToolCall
from atlas_agent.tool_registry import ToolRegistry


class ExecutionTarget(str, Enum):
    PI = "pi"
    PC = "pc"


class RouteStatus(str, Enum):
    READY = "ready"
    UNKNOWN_TOOL = "unknown_tool"
    UNSUPPORTED_TARGET = "unsupported_target"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    call_id: str
    tool_name: str
    status: RouteStatus
    target: ExecutionTarget | None
    reason: str

    @property
    def ready(self) -> bool:
        return self.status is RouteStatus.READY


class ToolRouter:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def decide(self, call: ToolCall) -> RouteDecision:
        try:
            tool = self._registry.get(call.tool_name)
        except KeyError:
            return RouteDecision(
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=RouteStatus.UNKNOWN_TOOL,
                target=None,
                reason=(
                    f"Tool is not registered: {call.tool_name}"
                ),
            )

        try:
            target = ExecutionTarget(tool.runs_on)
        except ValueError:
            return RouteDecision(
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=RouteStatus.UNSUPPORTED_TARGET,
                target=None,
                reason=(
                    f"Tool has unsupported execution target: "
                    f"{tool.runs_on}"
                ),
            )

        return RouteDecision(
            call_id=call.call_id,
            tool_name=call.tool_name,
            status=RouteStatus.READY,
            target=target,
            reason=(
                f"Route {call.tool_name} to {target.value}."
            ),
        )

    def decide_many(
        self,
        calls: list[ToolCall],
    ) -> list[RouteDecision]:
        return [
            self.decide(call)
            for call in calls
        ]
