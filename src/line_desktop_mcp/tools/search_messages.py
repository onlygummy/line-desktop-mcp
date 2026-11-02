"""The `search_messages` tool: find text across the local archive."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from line_desktop_mcp import lookup
from line_desktop_mcp.archive import Archive
from line_desktop_mcp.history import harvest
from line_desktop_mcp.reader import LineReader
from line_desktop_mcp.view import row_to_view

ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def register(mcp: FastMCP, reader: LineReader, archive: Archive) -> None:
    @mcp.tool(
        name="search_messages",
        title="Search LINE messages",
        tags={"line", "read"},
        annotations=ANNOTATIONS,
    )
    def search_messages(
        query: Annotated[
            str,
            Field(min_length=1, description="Text to look for; case-insensitive substring."),
        ],
        chats: Annotated[
            list[str] | None,
            "Chat mids or names to search. Omit to search everything archived.",
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum hits to return.")] = 20,
        since_days: Annotated[
            int | None,
            Field(ge=1, le=3650, description="Only messages from the last N days."),
        ] = None,
        sync: Annotated[
            bool,
            "Read the newest page of `chats` and page older messages from LINE "
            "first (slower; requires chats).",
        ] = False,
    ) -> dict[str, Any]:
        """Search archived messages for a piece of text, newest hits first.

        Searches the local archive, which holds only the messages this server
        has read. Read `coverage` before concluding that something does not
        exist: a small `messages_searched` means most history was never read.
        Pass `chats` to narrow the search to specific chats, and `sync=true` to
        read the newest page of those chats and page older messages from LINE
        first (at most three older pages per chat).

        Only plain text is searched: images, stickers, and sealed messages that
        could not be decrypted are not matched.
        """
        reader.ensure_ready()
        if not query.strip():
            raise ToolError("`query` must not be empty.")

        # Read the directory first: it saves chat names into the archive, so the
        # join below can label every hit even when `list_chats` was never called.
        directory = _directory_or_empty(reader, archive)
        chosen = lookup.scope(reader, archive, chats) if chats else []
        if sync and not chosen:
            raise ToolError(
                "sync=true needs `chats`: paging every chat would take minutes. "
                "Name the chats to scan, or leave sync off to search the archive."
            )

        api_pages = 0
        if sync:
            for chat in chosen:
                api_pages += harvest(reader, archive, str(chat["mid"]))["pages"]

        chat_mids = [str(chat["mid"]) for chat in chosen] or None
        since_ms = _since_ms(since_days)

        rows = archive.search(query, limit=limit, chat_mids=chat_mids, since_ms=since_ms)
        hits = [
            {
                "chat": {"mid": row.get("chat_mid"), "name": row.get("chat_name")},
                "message": row_to_view(row),
            }
            for row in rows
        ]

        return {
            "ok": True,
            "query": query,
            "scope": "archive+api" if sync else "archive",
            "hits": hits,
            "returned": len(hits),
            "chats_hit": _chats_hit(rows),
            "coverage": {
                "messages_searched": archive.count(chat_mids, since_ms),
                "chats_total": len(directory),
            },
            "api_pages": api_pages,
        }


def _chats_hit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which chats the hits came from, so the model can answer "where" directly."""
    counts: dict[str, dict[str, Any]] = {}
    for row in rows:
        mid = str(row.get("chat_mid") or "")
        entry = counts.setdefault(mid, {"mid": mid, "name": row.get("chat_name"), "hits": 0})
        entry["hits"] += 1
    return list(counts.values())


def _directory_or_empty(reader: LineReader, archive: Archive) -> list[dict[str, Any]]:
    try:
        return lookup.directory_or_archive(reader, archive)
    except ToolError:
        return []


def _since_ms(days: int | None) -> int | None:
    if not days:
        return None
    return int(time.time() * 1000) - int(days) * 86_400_000
