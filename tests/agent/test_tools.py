from atlas_agent.tools import AtlasTool


def test_atlas_tool_defaults_and_handler() -> None:
    def handler(value: int) -> int:
        return value + 1

    tool = AtlasTool(
        name="test.increment",
        description="Increment an integer.",
        runs_on="pi",
        handler=handler,
    )

    assert tool.permission_level == 0
    assert tool.timeout_seconds == 30
    assert tool.metadata == {}
    assert tool.handler(4) == 5
