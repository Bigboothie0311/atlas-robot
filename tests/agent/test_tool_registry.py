import pytest

from atlas_agent.tool_registry import ToolRegistry
from atlas_agent.tools import AtlasTool


def make_tool(name: str, runs_on: str = "pi") -> AtlasTool:
    return AtlasTool(
        name=name,
        description=f"Test tool {name}.",
        runs_on=runs_on,
        handler=lambda: name,
    )


def test_register_get_and_list_tools() -> None:
    registry = ToolRegistry()
    pi_tool = make_tool("system.status", runs_on="pi")
    pc_tool = make_tool("pc.search", runs_on="pc")

    registry.register(pc_tool)
    registry.register(pi_tool)

    assert len(registry) == 2
    assert "system.status" in registry
    assert registry.get("system.status") is pi_tool
    assert registry.list_tools() == [pc_tool, pi_tool]
    assert registry.list_tools(runs_on="pi") == [pi_tool]


def test_duplicate_registration_is_rejected() -> None:
    registry = ToolRegistry()
    tool = make_tool("system.status")

    registry.register(tool)

    with pytest.raises(ValueError, match="Tool already registered"):
        registry.register(tool)


def test_unregister_and_unknown_tool_errors() -> None:
    registry = ToolRegistry()
    tool = make_tool("system.status")
    registry.register(tool)

    assert registry.unregister("system.status") is tool
    assert "system.status" not in registry

    with pytest.raises(KeyError, match="Unknown tool"):
        registry.get("system.status")

    with pytest.raises(KeyError, match="Unknown tool"):
        registry.unregister("system.status")
