"""Page through LINE and archive what comes back.

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
    """Bring one chat up to date, then page backwards; returns what it cost.

    The newest page is always fetched first. Paging only backwards would never
    see a message that arrived after the chat was archived, so a search would
    silently miss it.

    Older pages start from the oldest message already archived, and the walk
    stops on an empty page or on one that adds nothing new, so a chat that ended
    yesterday costs the newest page plus one. `pages` counts every LINE page
    fetched, newest included.
    """
    total = _store(reader, archive, chat_mid, reader.recent(chat_mid, min(page_size, PAGE_SIZE)))
    used = 1

    cursor = archive.oldest(chat_mid)
    if cursor is None:
        return {"pages": used, "messages": total}

    cursor_id = str(cursor.get("id") or "")
    cursor_ms = int(cursor.get("delivered_ms") or 0)

    for _ in range(max(0, pages)):
        page = reader.previous(chat_mid, cursor_id, cursor_ms, page_size)
        if not page:
            break
        used += 1
        before = total
        total += _store(reader, archive, chat_mid, page)
        last = page[-1]
        cursor_id = str(last.get("id") or "")
        cursor_ms = _ms(last)
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
