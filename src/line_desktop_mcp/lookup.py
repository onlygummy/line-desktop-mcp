"""Chat lookup shared by the read tools (single responsibility: resolution).

`get_messages` and `search_messages` both need to turn a mid or a name into one
chat, and both must fall back to the archive when LINE is unreachable. Keeping
that in one place keeps the two tools' behaviour identical.
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
    """Chats from LINE, or from the archive when LINE cannot be reached."""
    try:
        return reader.directory(refresh=refresh)
    except ToolError:
        archived = archive.chats()
        if archived:
            return archived
        raise


def resolve(reader: LineReader, archive: Archive, ref: str) -> dict[str, Any]:
    """One chat by mid or name; ambiguity is reported, never guessed."""
    for refresh in (False, True):
        chat, candidates = match_chat(ref, directory(reader, archive, refresh=refresh))
        if chat:
            return chat
        if candidates:
            raise ToolError(_ambiguous(ref, candidates))
    raise ToolError(f"No chat matches {ref!r}. Call list_chats to see the available chats.")


def scope(reader: LineReader, archive: Archive, refs: list[str]) -> list[dict[str, Any]]:
    """Resolve several refs against one listing; any bad ref fails the call."""
    chats = directory(reader, archive)
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
