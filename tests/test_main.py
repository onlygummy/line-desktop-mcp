"""The MCP surface: six tools, five of them non-destructive."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from line_desktop_mcp.main import mcp


def _tools():
    async def go():
        async with Client(mcp) as client:
            return await client.list_tools()

    return asyncio.run(go())


def test_the_six_tools_are_exposed() -> None:
    tools = _tools()

    assert [tool.name for tool in tools] == [
        "login",
        "logout",
        "list_chats",
        "get_messages",
        "search_messages",
        "unread",
    ]


def test_unread_schema_and_description() -> None:
    unread = {tool.name: tool for tool in _tools()}["unread"]

    assert set(unread.input_schema["properties"]) == {
        "chats_limit",
        "per_chat",
        "include_images",
    }
    assert unread.annotations is not None
    assert unread.annotations.destructive_hint is False
    assert "chats_skipped" in unread.description


def test_logout_schema_and_annotations() -> None:
    logout = {tool.name: tool for tool in _tools()}["logout"]

    assert set(logout.input_schema["properties"]) == set()
    assert logout.annotations is not None
    assert logout.annotations.read_only_hint is False
    assert logout.annotations.destructive_hint is True
    assert "archive" in logout.description


def test_login_schema_and_annotations() -> None:
    login = {tool.name: tool for tool in _tools()}["login"]

    assert set(login.input_schema["properties"]) == {"timeout_sec", "force"}
    assert login.annotations is not None
    assert login.annotations.read_only_hint is False
    assert login.annotations.destructive_hint is False
    assert "Closing that window" in login.description


def test_read_tools_declare_their_local_side_effects() -> None:
    tools = {tool.name: tool for tool in _tools()}

    for name in ("list_chats", "get_messages", "search_messages", "unread"):
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
        "offset",
        "after",
        "before",
        "from_sender",
        "order",
        "since_days",
        "sync",
    }
    assert "coverage" in search.description
    assert "sealed" in search.description
