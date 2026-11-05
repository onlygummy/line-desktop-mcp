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

from line_desktop_mcp import attach, lookup, when
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
        after: Annotated[
            str | None,
            "Only messages on or after this date (`YYYY-MM-DD`) or ISO timestamp.",
        ] = None,
        before: Annotated[
            str | None,
            "Only messages on or before this date (`YYYY-MM-DD`). A bare date "
            "covers the whole day.",
        ] = None,
        around: Annotated[
            str | None,
            "A message id from an earlier result: read the messages around it "
            "instead of the newest ones. Answered from the archive.",
        ] = None,
        context: Annotated[
            int,
            Field(ge=1, le=50, description="Messages on each side when `around` is set."),
        ] = 5,
        include_images: Annotated[
            bool,
            "Attach image content blocks. The image message rows are still returned when false.",
        ] = True,
        max_images: Annotated[
            int | None,
            Field(ge=1, le=100, description="Maximum image attachments for this call."),
        ] = None,
        max_image_bytes: Annotated[
            int | None,
            Field(ge=1, description="Do not attach an image larger than this many bytes."),
        ] = None,
    ) -> list[Any]:
        """Read the newest messages of one LINE chat, oldest first.

        Pass `after` and `before` to read a time window instead of the newest
        slice: a bare date means local midnight, and `before` includes the whole
        day. The archive answers first, so a window that was read before is
        cheap and works offline; LINE is only paged back when the archive
        cannot fill `limit`.

        Pass `around` to read the messages around one id, which is how a
        search hit turns into its conversation. That answer comes from the
        archive only, so the id must have been read before; `context` sets how
        many messages appear on each side and `limit` is ignored. `around`
        cannot be combined with `after` or `before`.

        Returns a JSON result plus the images those messages contain. Messages
        are decrypted when the saved session holds the Letter Sealing keys; a
        sealed message that cannot be opened has `sealed: true` and no text.
        Every message read is stored in a local SQLite archive, and every image
        is saved under the media directory before it is attached, so the next
        call is cheaper and still works when LINE is unreachable.

        Pass `include_images=false` to keep the image message rows but attach
        no image blocks. Set `max_images` or `max_image_bytes` to cap the
        attachments; the ids that were not attached appear in
        `images_skipped`. Defaults leave the previous behaviour unchanged.

        Check `partial`: true means the page budget ran out before `limit`, so
        older messages were not reached. Check `stale`: true means LINE could
        not be reached and the result came from the local archive.
        """
        ready = reader.ensure_ready()
        resolved = lookup.resolve(reader, archive, chat)
        chat_mid = str(resolved["mid"])

        if around and (after or before):
            raise ToolError("`around` already selects a window; drop `after`/`before`.")

        after_ms, before_ms = when.window(after, before)
        if around:
            entries = _context(archive, chat_mid, around, context)
            stale, source, partial = False, "db", False
        else:
            entries, stale, source, partial = _collect(
                reader, archive, chat_mid, limit, after_ms, before_ms
            )
        ordered = sorted(entries.values(), key=lambda entry: entry["ms"])
        if not around:
            ordered = ordered[-limit:]

        # LINE does not put sender names in group messages, so resolve every
        # sender that is still unknown before rendering (one call per 100 mids).
        # An archive-only answer must not need LINE: with no raw messages there
        # is nothing to look up, and a failed lookup falls back to the mids.
        sender_mids = [entry["raw"].get("from") for entry in ordered if entry.get("raw")]
        try:
            names = reader.resolve_names(sender_mids) if sender_mids else {}
        except ToolError:
            names = {}
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

        image_items = [
            (
                str(entry["view"]["id"]),
                entry.get("raw") or attach.raw_payload(entry.get("row") or {}),
            )
            for entry in ordered
            if entry["view"].get("kind") == "image"
        ]
        if include_images:
            images, images_failed, images_skipped = attach.attach_images(
                reader,
                archive,
                chat_mid,
                image_items,
                max_images=max_images,
                max_image_bytes=max_image_bytes,
            )
        else:
            images, images_failed = [], []
            images_skipped = [message_id for message_id, _raw in image_items]

        payload = {
            "ok": True,
            "chat": {
                "mid": chat_mid,
                "name": resolved.get("name"),
                "kind": resolved.get("kind"),
            },
            "messages": [entry["view"] for entry in ordered],
            "returned": len(ordered),
            "window": {"after": after, "before": before},
            "around": around,
            "source": source,
            "e2ee_ready": ready.e2ee_ready,
            "stale": stale,
            "partial": partial,
            "images": [
                {"message_id": message_id, "path": str(path)} for message_id, path in images
            ],
            "images_failed": images_failed,
            "images_skipped": images_skipped,
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
    after_ms: int | None = None,
    before_ms: int | None = None,
) -> tuple[dict[str, dict[str, Any]], bool, str, bool]:
    """Gather the newest messages, or the newest messages inside one window."""
    if after_ms is None and before_ms is None:
        return _collect_newest(reader, archive, chat_mid, limit)
    return _collect_window(reader, archive, chat_mid, limit, after_ms, before_ms)


def _collect_newest(
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


def _collect_window(
    reader: LineReader,
    archive: Archive,
    chat_mid: str,
    limit: int,
    after_ms: int | None,
    before_ms: int | None,
) -> tuple[dict[str, dict[str, Any]], bool, str, bool]:
    """Gather the newest messages inside one time window.

    The archive answers first, so a window that was read before costs nothing
    and still works offline. LINE cannot jump to a date, so filling the rest
    means walking back from the newest message, at most `MAX_PAGES` pages.
    """
    entries: dict[str, dict[str, Any]] = {}
    from_archive: set[str] = set()
    for row in archive.messages_in(chat_mid, limit, after_ms=after_ms, before_ms=before_ms):
        _remember_row(entries, from_archive, row)

    if len(entries) >= limit:
        return entries, False, "db", False

    try:
        fresh = reader.recent(chat_mid, FRESH_BATCH)
    except ToolError:
        return entries, True, "db", False

    _remember_window(entries, fresh, after_ms, before_ms)
    reached_edge = not fresh
    cursor = fresh[-1] if fresh else None
    used = 0

    while cursor is not None and len(entries) < limit and used < MAX_PAGES:
        if after_ms is not None and _ms(cursor) < after_ms:
            reached_edge = True  # already past the window: nothing older matches
            break
        page = reader.previous(chat_mid, str(cursor.get("id") or ""), _ms(cursor), PAGE_BATCH)
        used += 1
        if not page:
            reached_edge = True
            break
        _remember_window(entries, page, after_ms, before_ms)
        cursor = page[-1]

    source = "mixed" if from_archive else "api"
    partial = len(entries) < limit and not reached_edge
    return entries, False, source, partial


def _remember_window(
    entries: dict[str, dict[str, Any]],
    messages: list[dict[str, Any]],
    after_ms: int | None,
    before_ms: int | None,
) -> None:
    """Remember only the messages that fall inside the window."""
    for message in messages:
        ms = _ms(message)
        if after_ms is not None and ms < after_ms:
            continue
        if before_ms is not None and ms >= before_ms:
            continue
        message_id = str(message.get("id") or "")
        if not message_id:
            continue
        existing = entries.get(message_id)
        if existing is not None and existing.get("raw"):
            continue
        # A LINE copy replaces an archived row, so a message that was sealed
        # when it was read can still be decrypted today.
        entries[message_id] = {"ms": ms, "raw": message}


def _context(
    archive: Archive, chat_mid: str, message_id: str, context: int
) -> dict[str, dict[str, Any]]:
    """The messages around one archived message: before, the anchor, after.

    Archive only. LINE has no API that walks forward, and the anchor came from
    a previous read, so everything needed is already stored.
    """
    anchor = archive.message(chat_mid, message_id)
    if anchor is None:
        raise ToolError(
            f"Message {message_id} is not archived for this chat. Read the chat "
            "with get_messages first, then retry with `around`."
        )
    anchor_ms = int(anchor.get("delivered_ms") or 0)
    entries: dict[str, dict[str, Any]] = {}
    from_archive: set[str] = set()
    for row in archive.messages(chat_mid, context, before_ms=anchor_ms):
        _remember_row(entries, from_archive, row)
    _remember_row(entries, from_archive, anchor)
    for row in archive.messages_after(chat_mid, anchor_ms, context):
        _remember_row(entries, from_archive, row)
    return entries


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
