"""Page backwards through LINE and archive what comes back.

Single responsibility: growing the archive. `search_messages(sync=True)` uses it
before searching, so a search can reach further back than what was read already.
"""

from __future__ import annotations

from typing import Any

from line_desktop_mcp.archive import Archive
from line_desktop_mcp.reader import LineReader
from line_desktop_mcp.view import to_row

#: How many older pages one harvest may fetch per chat.
DEFAULT_PAGES = 3
#: Messages asked for per page.
PAGE_SIZE = 200


def harvest(
    reader: LineReader,
    archive: Archive,
    chat_mid: str,
    *,
    pages: int = DEFAULT_PAGES,
    page_size: int = PAGE_SIZE,
) -> dict[str, int]:
    """Fetch older messages and store them; returns how much work was done.

    Starts from the oldest message already archived, or from the newest page
    when the chat is unknown. Stops early on an empty page or when a page adds
    nothing new, so a chat that ended yesterday costs one request.
    """
    total = 0
    oldest = archive.oldest(chat_mid)

    if oldest is None:
        fresh = reader.recent(chat_mid, min(page_size, PAGE_SIZE))
        if not fresh:
            return {"pages": 0, "messages": 0}
        total += _store(reader, archive, chat_mid, fresh)
        cursor = fresh[-1]
        cursor_id = str(cursor.get("id") or "")
        cursor_ms = _ms(cursor)
    else:
        cursor_id = str(oldest.get("id") or "")
        cursor_ms = int(oldest.get("delivered_ms") or 0)

    used = 0
    for _ in range(max(0, pages)):
        page = reader.previous(chat_mid, cursor_id, cursor_ms, page_size)
        if not page:
            break
        used += 1
        before = total
        total += _store(reader, archive, chat_mid, page)
        cursor = page[-1]
        cursor_id = str(cursor.get("id") or "")
        cursor_ms = _ms(cursor)
        if total == before:
            break

    return {"pages": used, "messages": total}


def _store(
    reader: LineReader,
    archive: Archive,
    chat_mid: str,
    messages: list[dict[str, Any]],
) -> int:
    """Archive the messages that are new; return how many were added."""
    known = archive.known_ids(str(message.get("id") or "") for message in messages)
    fresh = [
        message
        for message in messages
        if str(message.get("id") or "") and str(message.get("id")) not in known
    ]
    if not fresh:
        return 0
    names = reader.resolve_names(
        [str(message.get("from")) for message in fresh if message.get("from")]
    )
    archive.upsert_messages([to_row(message, names, chat_mid, reader.decrypt) for message in fresh])
    return len(fresh)


def _ms(message: dict[str, Any]) -> int:
    try:
        return int(message.get("deliveredTime") or message.get("createdTime") or 0)
    except (TypeError, ValueError):
        return 0
