"""The `search_messages` tool: find text across the local archive."""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from line_desktop_mcp import lookup, when
from line_desktop_mcp.archive import Archive
from line_desktop_mcp.history import harvest
from line_desktop_mcp.reader import LineReader
from line_desktop_mcp.view import KIND_NAMES, known_kind, row_to_view, snippet

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
            str | None,
            "Text to look for; case-insensitive substring. Omit it to search by "
            "`kinds` alone, for example every image.",
        ] = None,
        kinds: Annotated[
            list[str] | None,
            "Message kinds to keep, such as `image`, `file`, or `sticker`.",
        ] = None,
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
        match: Annotated[
            Literal["substring", "words"],
            "How `query` matches text. `substring` (default) needs the whole "
            "query in order; `words` needs every space-separated term, in any "
            "order.",
        ] = "substring",
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
        """Search archived messages for text, or for a message kind.

        Pass `query` to match text, `kinds` to match what a message is, or
        both. `kinds=["image"]` with no `query` is how you find photos and
        files, which have no text to match.

        Searches the local archive, which holds only the messages this server
        has read. Read `coverage` before concluding that something does not
        exist: a small `messages_scanned` means most history was never read.

        Narrow the search with `chats`, `after`/`before`, `from_sender`, and
        `since_days`. A bare date means local midnight, and `before` includes
        the whole day. When both `after` and `since_days` are given, `after`
        wins. `order="oldest"` answers "where did this start", and `offset`
        pages through more hits; `truncated: true` means more hits exist
        after this page.

        `from_sender` accepts a mid or part of a name, matched only against
        senders already archived. Pass `chats` to narrow the search, and
        `sync=true` to read the newest page of those chats and page older
        messages from LINE first (at most three older pages per chat).

        Text search only sees messages that have text: images, stickers, and
        sealed messages that could not be decrypted never match a `query`.
        Each hit carries a `snippet` around the match when `query` was given.

        `match="words"` needs every space-separated term of the query, in any
        order, so "invoice today" also finds "today's invoice". It helps Latin
        scripts only: Thai has no word separators, which makes the query one
        term either way.
        """
        reader.ensure_ready()
        text = query.strip() if query else None
        kinds = _kinds(kinds)
        if not text and not kinds:
            raise ToolError("Provide `query`, `kinds`, or both.")

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
        after_ms, before_ms = when.window(after, before)
        if after_ms is None:
            after_ms = _since_ms(since_days)
            when.check(after_ms, before_ms, field="since_days")

        sender = lookup.sender(archive, from_sender, chat_mids=chat_mids) if from_sender else None
        from_mid = sender["mid"] if sender else None

        rows = archive.search(
            text,
            # One extra row is how we know another page exists; the archive
            # itself never has to think about paging.
            limit=limit + 1,
            offset=offset,
            chat_mids=chat_mids,
            after_ms=after_ms,
            before_ms=before_ms,
            from_mid=from_mid,
            kinds=kinds,
            match=match,
            newest_first=order != "oldest",
        )
        truncated = len(rows) > limit
        rows = rows[:limit]
        coverage = archive.coverage(
            chat_mids=chat_mids,
            after_ms=after_ms,
            before_ms=before_ms,
            from_mid=from_mid,
            kinds=kinds,
        )
        hits = [
            {
                "chat": {"mid": row.get("chat_mid"), "name": row.get("chat_name")},
                "message": row_to_view(row),
                "snippet": snippet(row.get("text"), text) if text else None,
            }
            for row in rows
        ]

        return {
            "ok": True,
            "query": query,
            "scope": "archive+api" if sync else "archive",
            "filters": {
                "chats": chat_mids,
                "kinds": kinds,
                "after": after,
                "before": before,
                "from": sender,
                "order": order,
                "match": match,
                "offset": offset,
            },
            "hits": hits,
            "returned": len(hits),
            "truncated": truncated,
            "chats_hit": _chats_hit(rows),
            "coverage": {
                # Without a text query every in-scope message is a candidate,
                # so `scanned` would read as zero and mean nothing.
                "messages_scanned": coverage["scanned"] if text else coverage["in_scope"],
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


def _kinds(values: list[str] | None) -> list[str] | None:
    """Normalise a `kinds` filter; an unknown name is refused, not ignored."""
    if not values:
        return None
    cleaned = [value.strip().lower() for value in values if value and value.strip()]
    unknown = [value for value in cleaned if not known_kind(value)]
    if unknown:
        allowed = ", ".join(sorted(KIND_NAMES))
        raise ToolError(f"Unknown kind(s): {', '.join(unknown)}. Use one of {allowed}, or typeN.")
    return list(dict.fromkeys(cleaned)) or None
