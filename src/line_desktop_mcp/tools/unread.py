"""The `unread` tool: everything that needs attention, in one call.

`get_messages` answers "read this chat". This answers "what did I miss", so it
stops at the newest few messages of each unread chat and never pages back.
Every message it reads is archived like any other read, and nothing is marked
as read on LINE.
"""

from __future__ import annotations

import json
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

#: Chats are capped because each one costs a LINE request behind the call lock.
CHATS_LIMIT = 10
#: Messages per chat: enough to follow the conversation, not the whole history.
PER_CHAT = 10

ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def register(mcp: FastMCP, reader: LineReader, archive: Archive) -> None:
    @mcp.tool(
        name="unread",
        title="Read unread messages",
        tags={"line", "read"},
        annotations=ANNOTATIONS,
    )
    def unread(
        chats_limit: Annotated[
            int,
            Field(ge=1, le=20, description="How many unread chats to summarise."),
        ] = CHATS_LIMIT,
        per_chat: Annotated[
            int,
            Field(ge=1, le=50, description="How many newest messages per chat."),
        ] = PER_CHAT,
        include_images: Annotated[
            bool,
            Field(description="Attach the images in those messages (slower and larger)."),
        ] = False,
    ) -> list[Any]:
        """List chats with unread messages and the newest messages in each.

        One call instead of one `get_messages` per chat. Chats are ordered by
        their latest activity, and `chats_skipped` says how many unread chats
        did not fit in `chats_limit`. Every message read is archived, and
        nothing is marked as read on LINE: the counts stay until the chat is
        opened on the phone.

        It costs one LINE request per chat, so the caps are low on purpose.
        Images are downloaded only with `include_images=true`, because a digest
        of ten chats can hold dozens. When LINE cannot be reached the archive
        answers instead, and `stale` says so.
        """
        ready = reader.ensure_ready()
        directory, directory_stale = lookup.directory_with_state(reader, archive)

        pending = [chat for chat in directory if int(chat.get("unread") or 0) > 0]
        pending.sort(key=lambda chat: int(chat.get("last_ms") or 0), reverse=True)
        chosen = pending[:chats_limit]

        entries: list[dict[str, Any]] = []
        blocks: list[Any] = []
        for chat in chosen:
            entry, attached = _digest_chat(reader, archive, chat, per_chat, include_images)
            entries.append(entry)
            blocks.extend(Image(path=str(path)) for _message_id, path in attached)

        payload = {
            "ok": True,
            "chats": entries,
            "chats_returned": len(entries),
            "chats_skipped": len(pending) - len(entries),
            "unread_total": sum(int(chat.get("unread") or 0) for chat in pending),
            "messages_returned": sum(entry["returned"] for entry in entries),
            "source": _source(directory_stale, entries),
            "stale": directory_stale,
            "e2ee_ready": ready.e2ee_ready,
        }

        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False)), *blocks]


def _digest_chat(
    reader: LineReader,
    archive: Archive,
    chat: dict[str, Any],
    per_chat: int,
    include_images: bool,
) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    """Newest messages of one unread chat: LINE first, the archive if that fails."""
    chat_mid = str(chat.get("mid") or "")
    stale = False
    try:
        raw = reader.recent(chat_mid, per_chat)
    except ToolError:
        raw = []
        stale = True

    raw.sort(key=_ms)
    raw = raw[-per_chat:]

    if raw:
        try:
            names = reader.resolve_names(
                [str(message.get("from")) for message in raw if message.get("from")]
            )
        except ToolError:
            names = {}  # the mids still render; the text is what matters
        archive.upsert_messages(
            [to_row(message, names, chat_mid, reader.decrypt) for message in raw]
        )
        views = [to_view(message, names, reader.decrypt) for message in raw]
        payloads = {str(message.get("id")): message for message in raw}
    else:
        rows = archive.messages(chat_mid, per_chat)
        views = [row_to_view(row) for row in rows]
        payloads = {str(row.get("id")): attach.raw_payload(row) for row in rows}
        stale = stale or bool(rows)

    attached: list[tuple[str, Any]] = []
    failed: list[str] = []
    if include_images:
        items = [
            (str(view["id"]), payloads.get(str(view["id"])) or {})
            for view in views
            if view.get("kind") == "image"
        ]
        attached, failed, _skipped = attach.attach_images(reader, archive, chat_mid, items)

    entry = {
        "chat": {"mid": chat_mid, "name": chat.get("name"), "kind": chat.get("kind")},
        "unread": int(chat.get("unread") or 0),
        "messages": views,
        "returned": len(views),
        "stale": stale,
        "images": [{"message_id": message_id, "path": str(path)} for message_id, path in attached],
        "images_failed": failed,
    }
    return entry, attached


def _source(directory_stale: bool, entries: list[dict[str, Any]]) -> str:
    """Same vocabulary as the other read tools: `api`, `mixed`, or `db`."""
    from_archive = directory_stale or any(entry["stale"] for entry in entries)
    if not from_archive:
        return "api"
    if not entries or all(entry["stale"] for entry in entries):
        return "db"
    return "mixed"


def _ms(message: dict[str, Any]) -> int:
    try:
        return int(message.get("deliveredTime") or message.get("createdTime") or 0)
    except (TypeError, ValueError):
        return 0
