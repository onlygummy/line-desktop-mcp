"""The MCP surface: exactly one tool, and it is not advertised as read-only."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from line_desktop_mcp.main import mcp


def _tools():
    async def go():
        async with Client(mcp) as client:
            return await client.list_tools()

    return asyncio.run(go())


def test_only_the_login_tool_is_exposed() -> None:
    tools = _tools()

    assert [tool.name for tool in tools] == ["line_login"]


def test_login_schema_and_annotations() -> None:
    (tool,) = _tools()

    assert set(tool.input_schema["properties"]) == {"timeout_sec", "force"}
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert "Closing that window" in tool.description
