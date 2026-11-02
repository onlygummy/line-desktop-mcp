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


def _ambiguous(ref: str, candidates: list[dict[str, Any]]) -> str:
    listing = ", ".join(f"{item.get('name')} ({item.get('mid')})" for item in candidates[:10])
    return f"{len(candidates)} chats match {ref!r}: {listing}. Retry with the mid."
