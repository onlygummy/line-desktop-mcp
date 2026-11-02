"""The `get_messages` tool: read one chat, archive it, hand images to the model.

The flow is deliberately hybrid:

1. the newest slice always comes from LINE, so a chat is never shown stale;
2. older messages are filled from the local archive first (cheap, offline-safe);
3. only when the archive is short does the tool page backwards through LINE.

Every message it returns is written to the archive, and every image is saved
under the media directory as a backup before being attached to the result.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.utilities.types import Image
from mcp.types import TextContent, ToolAnnotations
from pydantic import Field

from line_desktop_mcp import attach, lookup
from line_desktop_mcp.archive import Archive
from line_desktop_mcp.reader import LineReader
from line_desktop_mcp.view import row_to_view, to_row, to_view

#: Newest messages always pulled from LINE on every call.
FRESH_BATCH = 50
#: Size of one backwards page when the archive cannot fill the request.
PAGE_BATCH = 200
#: How many backwards pages one call may cost.
MAX_PAGES = 5

ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def register(mcp: FastMCP, reader: LineReader, archive: Archive) -> None:
    @mcp.tool(
        name="get_messages",
        title="Read LINE messages",
        tags={"line", "read"},
        annotations=ANNOTATIONS,
    )
    def get_messages(
        chat: Annotated[
            str,
            "Chat mid, or part of the chat name. Use list_chats to find it.",
        ],
        limit: Annotated[
            int, Field(ge=1, le=200, description="How many messages, newest ones.")
        ] = 30,
    ) -> list[Any]:
        """Read the newest messages of one LINE chat, oldest first.

        Returns a JSON result plus the images those messages contain. Messages
        are decrypted when the saved session holds the Letter Sealing keys; a
        sealed message that cannot be opened has `sealed: true` and no text.
        Every message read is stored in a local SQLite archive, and every image
        is saved under the media directory before it is attached, so the next
        call is cheaper and still works when LINE is unreachable.

        Check `partial`: true means the page budget ran out before `limit`, so
        older messages were not reached. Check `stale`: true means LINE could
        not be reached and the result came from the local archive.
        """
        ready = reader.ensure_ready()
        resolved = lookup.resolve(reader, archive, chat)
        chat_mid = str(resolved["mid"])

        entries, stale, source, partial = _collect(reader, archive, chat_mid, limit)
        ordered = sorted(entries.values(), key=lambda entry: entry["ms"])[-limit:]

        # LINE does not put sender names in group messages, so resolve every
        # sender that is still unknown before rendering (one call per 100 mids).
        names = reader.resolve_names(
            [entry["raw"].get("from") for entry in ordered if entry.get("raw")]
        )
        archive.upsert_messages(
            [
                to_row(entry["raw"], names, chat_mid, reader.decrypt)
                for entry in ordered
                if entry.get("raw")
            ]
        )
        for entry in ordered:
            entry["view"] = (
                to_view(entry["raw"], names, reader.decrypt)
                if entry.get("raw")
                else row_to_view(entry["row"], names)
            )

        images, images_failed = attach.attach_images(
            reader,
            archive,
            chat_mid,
            [
                (
                    str(entry["view"]["id"]),
                    entry.get("raw") or attach.raw_payload(entry.get("row") or {}),
                )
                for entry in ordered
                if entry["view"].get("kind") == "image"
            ],
        )

        payload = {
            "ok": True,
            "chat": {
                "mid": chat_mid,
                "name": resolved.get("name"),
                "kind": resolved.get("kind"),
            },
            "messages": [entry["view"] for entry in ordered],
            "returned": len(ordered),
            "source": source,
            "e2ee_ready": ready.e2ee_ready,
            "stale": stale,
            "partial": partial,
            "images": [
                {"message_id": message_id, "path": str(path)} for message_id, path in images
            ],
            "images_failed": images_failed,
        }

        blocks: list[Any] = [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
        blocks.extend(Image(path=str(path)) for _message_id, path in images)
        return blocks


# -- collection --------------------------------------------------------------
def _collect(
    reader: LineReader,
    archive: Archive,
    chat_mid: str,
    limit: int,
) -> tuple[dict[str, dict[str, Any]], bool, str, bool]:
    """Gather up to `limit` messages: newest from LINE, older from the archive.

    Returns `(entries, stale, source, partial)`. `partial` is only true when the
    call stopped on its page budget; a chat that simply has fewer messages than
    asked is not partial, and `returned` already says how many arrived.
    """
    entries: dict[str, dict[str, Any]] = {}
    from_archive: set[str] = set()
    stale = False
    exhausted = False

    try:
        fresh = reader.recent(chat_mid, min(limit, FRESH_BATCH))
    except ToolError:
        fresh = []
        stale = True

    if len(fresh) < min(limit, FRESH_BATCH):
        exhausted = True  # LINE had nothing more to give on the newest page

    _remember(entries, fresh)

    # Older messages: the archive first, which costs nothing and works offline.
    if entries:
        missing = limit - len(entries)
        if missing > 0:
            oldest_ms = min(entry["ms"] for entry in entries.values())
            for row in archive.messages(chat_mid, missing, before_ms=oldest_ms):
                _remember_row(entries, from_archive, row)
    elif stale:
        for row in archive.messages(chat_mid, limit):
            _remember_row(entries, from_archive, row)

    # Still short: page backwards through LINE, up to the budget.
    if len(entries) < limit and fresh and not stale:
        cursor = fresh[-1]
        for _ in range(MAX_PAGES):
            page = reader.previous(chat_mid, str(cursor.get("id") or ""), _ms(cursor), PAGE_BATCH)
            if not page:
                exhausted = True
                break
            before = len(entries)
            _remember(entries, page)
            cursor = page[-1]
            if len(entries) == before:
                exhausted = True  # no progress: treat as the oldest page
                break
            if len(entries) >= limit:
                break

    source = "db" if stale else ("mixed" if from_archive else "api")
    partial = len(entries) < limit and not exhausted
    return entries, stale, source, partial


def _remember(entries: dict[str, dict[str, Any]], messages: list[dict[str, Any]]) -> None:
    for message in messages:
        message_id = str(message.get("id") or "")
        if not message_id or message_id in entries:
            continue
        entries[message_id] = {"ms": _ms(message), "raw": message}


def _remember_row(
    entries: dict[str, dict[str, Any]],
    from_archive: set[str],
    row: Mapping[str, Any],
) -> None:
    message_id = str(row.get("id") or "")
    if not message_id or message_id in entries:
        return
    entries[message_id] = {"ms": int(row.get("delivered_ms") or 0), "row": row}
    from_archive.add(message_id)


# -- helpers -----------------------------------------------------------------
def _ms(message: Mapping[str, Any]) -> int:
    try:
        return int(message.get("deliveredTime") or message.get("createdTime") or 0)
    except (TypeError, ValueError):
        return 0
