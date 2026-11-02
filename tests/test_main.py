"""The MCP surface: three tools, none of them advertised as read-only."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from line_desktop_mcp.main import mcp


def _tools():
    async def go():
        async with Client(mcp) as client:
            return await client.list_tools()

    return asyncio.run(go())


def test_the_four_tools_are_exposed() -> None:
    tools = _tools()

    assert [tool.name for tool in tools] == [
        "login",
        "list_chats",
        "get_messages",
        "search_messages",
    ]


def test_login_schema_and_annotations() -> None:
    login = {tool.name: tool for tool in _tools()}["login"]

    assert set(login.input_schema["properties"]) == {"timeout_sec", "force"}
    assert login.annotations is not None
    assert login.annotations.read_only_hint is False
    assert login.annotations.destructive_hint is False
    assert "Closing that window" in login.description


def test_read_tools_declare_their_local_side_effects() -> None:
    tools = {tool.name: tool for tool in _tools()}

    for name in ("list_chats", "get_messages", "search_messages"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is False, name
        assert annotations.destructive_hint is False, name


def test_get_messages_schema_and_description() -> None:
    read = {tool.name: tool for tool in _tools()}["get_messages"]

    assert set(read.input_schema["properties"]) == {"chat", "limit"}
    assert "archive" in read.description
    assert "stale" in read.description


def test_search_messages_schema_and_description() -> None:
    search = {tool.name: tool for tool in _tools()}["search_messages"]

    assert set(search.input_schema["properties"]) == {
        "query",
        "chats",
        "limit",
        "since_days",
        "sync",
    }
    assert "coverage" in search.description
    assert "sealed" in search.description
