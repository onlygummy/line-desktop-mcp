"""The `list_chats` tool: which chats exist, and what is unread."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from line_desktop_mcp import lookup
from line_desktop_mcp.archive import Archive
from line_desktop_mcp.reader import LineReader

# Listing writes to the local archive (names, kinds, unread), so it is honest
# to say this tool touches the machine -- but it never changes anything in LINE.
ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def register(mcp: FastMCP, reader: LineReader, archive: Archive) -> None:
    @mcp.tool(
        name="list_chats", title="List LINE chats", tags={"line", "read"}, annotations=ANNOTATIONS
    )
    def list_chats(
        unread_only: Annotated[bool, "Only chats that have unread messages."] = False,
        query: Annotated[str | None, "Case-insensitive substring filter on the chat name."] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 30,
    ) -> dict[str, Any]:
        """List your LINE chats with name, kind, unread count and last preview.

        Call this before `get_messages` to find the chat you want. A direct
        chat is named after the other person; a group keeps its LINE name.
        Nothing is marked as read. The listing is cached for a few minutes and
        archived locally, so it still answers when LINE cannot be reached
        (`stale: true` says the data came from the archive).
        """
        reader.ensure_ready()

        stale = False
        try:
            chats = lookup.directory(reader, archive)
            if query and not _filter(chats, unread_only, query):
                # LINE caps the listing, so a miss gets one refetch.
                chats = lookup.directory(reader, archive, refresh=True)
        except ToolError:
            chats = archive.chats()
            if not chats:
                raise
            stale = True

        chats = _filter(chats, unread_only, query)

        shown = chats[:limit]
        return {
            "ok": True,
            "chats": shown,
            "returned": len(shown),
            "unread_total": sum(int(chat.get("unread") or 0) for chat in chats),
            "stale": stale,
        }


def _filter(
    chats: list[dict[str, Any]], unread_only: bool, query: str | None
) -> list[dict[str, Any]]:
    result = chats
    if unread_only:
        result = [chat for chat in result if int(chat.get("unread") or 0) > 0]
    if query:
        needle = query.strip().casefold()
        result = [chat for chat in result if needle in str(chat.get("name") or "").casefold()]
    return result
