"""The `search_messages` tool: find text across the local archive."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

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
        offset: Annotated[
            int,
            Field(ge=0, le=1000, description="Hits to skip, for paging through results."),
        ] = 0,
        after: Annotated[
            str | None,
            "Only messages on or after this date (`YYYY-MM-DD`) or ISO timestamp.",
        ] = None,
        before: Annotated[
            str | None,
            "Only messages on or before this date (`YYYY-MM-DD`), or an ISO "
            "timestamp treated as an exclusive cut-off.",
        ] = None,
        from_sender: Annotated[
            str | None,
            "Sender mid, or part of their display name, as archived.",
        ] = None,
        order: Annotated[
            Literal["newest", "oldest"],
            "Newest hit first (default), or oldest first to find where a topic started.",
        ] = "newest",
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
        """Search archived messages for a piece of text.

        Searches the local archive, which holds only the messages this server
        has read. Read `coverage` before concluding that something does not
        exist: a small `messages_scanned` means most history was never read.

        Narrow the search with `chats`, `after`/`before`, `from_sender`, and
        `since_days`. A bare date means local midnight, and `before` includes
        the whole day. When both `after` and `since_days` are given, `after`
        wins. `order="oldest"` answers "where did this start", and `offset`
        pages through more hits; `truncated: true` means the limit was reached
        and there may be more.

        `from_sender` accepts a mid or part of a name, matched only against
        senders already archived. Pass `chats` to narrow the search, and
        `sync=true` to read the newest page of those chats and page older
        messages from LINE first (at most three older pages per chat).

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
        after_ms = _after_ms(after) if after else _since_ms(since_days)
        before_ms = _before_ms(before) if before else None
        if after_ms is not None and before_ms is not None and after_ms >= before_ms:
            raise ToolError("`after` must be earlier than `before`.")

        sender = lookup.sender(archive, from_sender, chat_mids=chat_mids) if from_sender else None
        from_mid = sender["mid"] if sender else None

        rows = archive.search(
            query,
            limit=limit,
            offset=offset,
            chat_mids=chat_mids,
            after_ms=after_ms,
            before_ms=before_ms,
            from_mid=from_mid,
            newest_first=order != "oldest",
        )
        coverage = archive.coverage(
            chat_mids=chat_mids, after_ms=after_ms, before_ms=before_ms, from_mid=from_mid
        )
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
            "filters": {
                "chats": chat_mids,
                "after": after,
                "before": before,
                "from": sender,
                "order": order,
                "offset": offset,
            },
            "hits": hits,
            "returned": len(hits),
            "truncated": len(rows) >= limit,
            "chats_hit": _chats_hit(rows),
            "coverage": {
                "messages_scanned": coverage["scanned"],
                "messages_in_scope": coverage["in_scope"],
                "messages_sealed": coverage["sealed"],
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


def _after_ms(value: str) -> int:
    return _moment_ms(value, "after")


def _before_ms(value: str) -> int:
    """A bare date covers the whole day, so `before=2026-11-30` includes it."""
    raw = value.strip()
    try:
        day = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return _moment_ms(raw, "before")
    return int((day + timedelta(days=1)).timestamp() * 1000)


def _moment_ms(value: str, field: str) -> int:
    """A local date or ISO timestamp to epoch milliseconds."""
    raw = value.strip()
    try:
        return int(datetime.strptime(raw, "%Y-%m-%d").timestamp() * 1000)
    except ValueError:
        pass
    try:
        return int(datetime.fromisoformat(raw).timestamp() * 1000)
    except ValueError:
        raise ToolError(f"`{field}` must be a date like 2026-11-01, or an ISO timestamp.") from None
