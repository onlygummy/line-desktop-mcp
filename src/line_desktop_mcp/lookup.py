"""Chat lookup shared by the read tools (single responsibility: resolution).

`get_messages` and `search_messages` both need to turn a mid or a name into one
chat, and both must fall back to the archive when LINE is unreachable. Keeping
that in one place keeps the two tools' behaviour identical.

The same function also persists what LINE returned: every read tool sees the
directory, and a name that was never written down is a name the archive cannot
give back when LINE is offline.
"""

from __future__ import annotations

from typing import Any

from fastmcp.exceptions import ToolError

from line_desktop_mcp.archive import Archive
from line_desktop_mcp.reader import LineReader
from line_desktop_mcp.view import match_chat


def directory(
    reader: LineReader, archive: Archive, *, refresh: bool = False
) -> list[dict[str, Any]]:
    """Chats from LINE, saved on the way so names outlive the session.

    Only this path writes. The archive fallback below must not stamp yesterday's
    unread counts back as if they were fresh.
    """
    chats = reader.directory(refresh=refresh)
    archive.upsert_chats(chats)
    return chats


def directory_with_state(
    reader: LineReader, archive: Archive, *, refresh: bool = False
) -> tuple[list[dict[str, Any]], bool]:
    """Chats, plus whether they came from the archive instead of LINE."""
    try:
        return directory(reader, archive, refresh=refresh), False
    except ToolError:
        archived = archive.chats()
        if archived:
            return archived, True
        raise


def directory_or_archive(
    reader: LineReader, archive: Archive, *, refresh: bool = False
) -> list[dict[str, Any]]:
    """Chats from LINE, or from the archive when LINE cannot be reached."""
    return directory_with_state(reader, archive, refresh=refresh)[0]


def resolve(reader: LineReader, archive: Archive, ref: str) -> dict[str, Any]:
    """One chat by mid or name; ambiguity is reported, never guessed."""
    for refresh in (False, True):
        chat, candidates = match_chat(ref, directory_or_archive(reader, archive, refresh=refresh))
        if chat:
            return chat
        if candidates:
            raise ToolError(_ambiguous(ref, candidates))
    raise ToolError(f"No chat matches {ref!r}. Call list_chats to see the available chats.")


def scope(reader: LineReader, archive: Archive, refs: list[str]) -> list[dict[str, Any]]:
    """Resolve several refs against one listing; any bad ref fails the call."""
    chats = directory_or_archive(reader, archive)
    chosen: list[dict[str, Any]] = []
    for ref in refs:
        chat, candidates = match_chat(ref, chats)
        if chat:
            chosen.append(chat)
            continue
        if candidates:
            raise ToolError(_ambiguous(ref, candidates))
        raise ToolError(f"No chat matches {ref!r}. Call list_chats to see the available chats.")
    return chosen


def sender(archive: Archive, ref: str, *, chat_mids: list[str] | None = None) -> dict[str, Any]:
    """One archived sender by mid or name; ambiguity is reported, never guessed.

    Only the archive is consulted. A sender with no archived message has no
    hits to find, so asking LINE would add a request and change nothing.
    """
    rows = archive.senders(chat_mids)
    if any(str(row.get("from_mid")) == ref for row in rows):
        return {"mid": ref, "name": _latest_name(rows, ref)}

    needle = ref.strip().casefold()
    if not needle:
        raise ToolError("`from_sender` must not be blank.")
    for match in (
        [row for row in rows if str(row.get("from_name") or "").casefold() == needle],
        [row for row in rows if needle in str(row.get("from_name") or "").casefold()],
    ):
        if match:
            return _one_sender(ref, match, rows)
    raise ToolError(
        f"No archived sender matches {ref!r}. Leave `from_sender` out to search "
        "every sender, or read that chat first so LINE returns their name."
    )


def _one_sender(
    ref: str, matches: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    mids = list(dict.fromkeys(str(row.get("from_mid")) for row in matches))
    if len(mids) > 1:
        listing = ", ".join(f"{_latest_name(rows, mid)} ({mid})" for mid in mids[:10])
        raise ToolError(f"{len(mids)} senders match {ref!r}: {listing}. Retry with the mid.")
    mid = mids[0]
    return {"mid": mid, "name": _latest_name(rows, mid)}


def _latest_name(rows: list[dict[str, Any]], mid: str) -> str | None:
    """The name on that sender's most recent archived message, if any."""
    for row in rows:
        if str(row.get("from_mid")) == mid and row.get("from_name"):
            return str(row["from_name"])
    return None


def _ambiguous(ref: str, candidates: list[dict[str, Any]]) -> str:
    listing = ", ".join(f"{item.get('name')} ({item.get('mid')})" for item in candidates[:10])
    return f"{len(candidates)} chats match {ref!r}: {listing}. Retry with the mid."
