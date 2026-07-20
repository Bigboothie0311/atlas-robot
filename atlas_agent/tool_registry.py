from __future__ import annotations

from atlas_agent.tools import AtlasTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, AtlasTool] = {}

    def register(self, tool: AtlasTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")

        self._tools[tool.name] = tool

    def unregister(self, name: str) -> AtlasTool:
        try:
            return self._tools.pop(name)
        except KeyError:
            raise KeyError(f"Unknown tool: {name}") from None

    def get(self, name: str) -> AtlasTool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"Unknown tool: {name}") from None

    def list_tools(self, runs_on: str | None = None) -> list[AtlasTool]:
        tools = self._tools.values()

        if runs_on is not None:
            tools = (tool for tool in tools if tool.runs_on == runs_on)

        return sorted(tools, key=lambda tool: tool.name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
