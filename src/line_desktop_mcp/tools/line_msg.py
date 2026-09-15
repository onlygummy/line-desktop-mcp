"""LINE message tools (single responsibility: wrap line-ext-msg as MCP tools).

Each tool opens its own LineClient and closes it on return, so no Chrome
process or login session leaks between calls. All calls use quiet mode to
keep stdout clean for stdio transport. Only rendered rows are visible
(virtualized list), so wide ranges still return just what the DOM holds.
"""

import asyncio
from dataclasses import asdict
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from line_ext_msg import (
    LineClient,
    LineError,
    Room,
    RoomNotFound,
)
from mcp.types import ToolAnnotations
from pydantic import Field

# Shared hints: every tool here only reads, repeats safely, and reaches
# out to a local browser. Clients use these to skip confirmations.
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

# Follow-up guidance per error kind, so the model tells the user what to
# do next instead of just reporting failure.
_NEXT = {
    "LoginRequired": (
        "The login was cancelled or timed out. Ask the user to scan the QR "
        "in the LINE login dialog (entering the PIN on their phone if "
        "asked), then call line_status again with wait_for_login=true. "
        "Leave Chrome running: the session lives in that Chrome process, "
        "so closing it forces a new QR scan."
    ),
    "QrDialogFailed": (
        "The QR dialog could not open on this machine. Ask the user to "
        "run line_status in an interactive desktop session, or log in "
        "manually, then retry."
    ),
    "ExtensionMissing": (
        "The extension install did not finish (its Chrome window was "
        "closed). Ask the user to retry and install the LINE extension "
        "from the Web Store page that opens; the call keeps polling and "
        "continues by itself as soon as the install completes."
    ),
    "RoomNotFound": (
        "Call list_rooms to show available rooms, then retry with a "
        "data-mid or exact name instead of an index."
    ),
}


def _ok(data: Any) -> dict:
    """Success envelope (single shape so callers branch on `ok`)."""
    return {"ok": True, "data": data}


def _fail(error: LineError) -> dict:
    """Domain failure envelope (lets the model branch on `error` kind)."""
    out = {"ok": False, "error": type(error).__name__, "message": str(error)}
    hint = _NEXT.get(type(error).__name__)
    if hint:
        out["next"] = hint
    return out


def _status_steps(wait_for_login: bool, timeout_sec: int = 180) -> list:
    """Run the readiness checklist, optionally waiting for login.

    Kept at module level for testability. With wait_for_login the library
    runs the whole startup sequence in one call: install the extension if
    missing, wait for the app, then show the QR dialog and wait until the
    user scans it or closes it. That matches `line-ext-msg` on the CLI.
    Without it the call is fail-fast and raises LoginRequired instead.
    timeout_sec only bounds the headed fallback (the QR dialog is
    unbounded). Raises LineError on the first failure.
    """
    with LineClient(quiet=True) as line:
        if wait_for_login:
            return line.status(
                wait_for_login=True,
                login_timeout_ms=timeout_sec * 1000,
            )
        return line.status()


def _resolve_ref(ref: int | str, rooms: list[Room]) -> Room:
    """Match a room by index, data-mid, or name substring (public API only)."""
    if isinstance(ref, int):
        for room in rooms:
            if room.index == ref:
                return room
    else:
        for room in rooms:
            if room.id == ref:
                return room
        for room in rooms:
            if ref in room.name:
                return room
    raise RoomNotFound(ref, [r.name for r in rooms])


def _resolve_targets(refs: list[int | str], all_rooms: list[Room]) -> tuple[list[Room], list[dict]]:
    """Resolve room refs against one listing; bad refs become error entries.

    The caller lists rooms once and passes it in, so N refs cost one
    listing. Unresolvable refs never crash the search: each becomes an
    entry the AI can report and skip.
    """
    targets: list[Room] = []
    skipped: list[dict] = []
    for ref in refs:
        try:
            targets.append(_resolve_ref(ref, all_rooms))
        except RoomNotFound as e:
            skipped.append(
                {
                    "room": ref,
                    "error": "RoomNotFound",
                    "message": str(e),
                    "next": _NEXT["RoomNotFound"],
                }
            )
    return targets, skipped


def _notify(loop: asyncio.AbstractEventLoop, ctx: Context, progress: int, total: int) -> None:
    """Best-effort progress from a worker thread (never fails the tool)."""
    try:
        fut = asyncio.run_coroutine_threadsafe(
            ctx.report_progress(progress=progress, total=total), loop
        )
        fut.result(timeout=5)
    except Exception:
        pass


def register_line_tools(mcp: FastMCP) -> None:
    """Register read-only LINE tools (no file writes, no media downloads)."""

    @mcp.tool(
        title="LINE status",
        tags={"line", "read"},
        annotations=READ_ONLY,
    )
    def line_status(
        wait_for_login: Annotated[
            bool,
            "True (default) waits for LINE login and shows the QR dialog. "
            "False does a fail-fast check and reports LoginRequired instead.",
        ] = True,
    ) -> dict:
        """Check the 5 readiness steps; by default also wait for login.

        Call this first: with the default it runs the whole setup in one
        continuous call (install the extension if missing, wait for the
        app, then show the QR dialog and wait until the user scans it or
        closes it), like `line-ext-msg` on the CLI. When already logged in
        it returns at once. Pass wait_for_login=false for a quick
        fail-fast check. No tool-level timeout is set.
        """
        try:
            steps = _status_steps(wait_for_login)
        except LineError as e:
            return _fail(e)
        return _ok([asdict(s) for s in steps])

    @mcp.tool(
        title="List LINE rooms",
        tags={"line", "read"},
        annotations=READ_ONLY,
    )
    def list_rooms(
        unread_only: Annotated[bool, "True narrows to rooms with unread > 0."] = False,
        query: Annotated[str | None, "Substring filter on room name."] = None,
    ) -> dict:
        """List chat rooms with unread counts and last-message preview.

        Prefer data-mid or name from the result when calling other tools:
        index values shift when rooms reorder.
        """
        try:
            with LineClient(quiet=True) as line:
                rooms = line.list_rooms(unread_only=unread_only, query=query)
            return _ok([asdict(r) for r in rooms])
        except LineError as e:
            return _fail(e)

    @mcp.tool(
        title="Read LINE messages",
        tags={"line", "read"},
        annotations=READ_ONLY,
    )
    def get_messages(
        room: Annotated[
            int | str | None,
            "Room index, data-mid, or name substring. None reads the current view. "
            "Prefer data-mid or name: index shifts when rooms reorder.",
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 5,
        date: Annotated[str | None, "Single day as YYYY-MM-DD."] = None,
        date_from: Annotated[str | None, "Range start as YYYY-MM-DD."] = None,
        date_to: Annotated[str | None, "Range end as YYYY-MM-DD."] = None,
        sender: Annotated[str | None, "Substring filter on sender name."] = None,
        keyword: Annotated[str | None, "Substring filter on message text."] = None,
        scroll: Annotated[
            bool,
            "True scrolls up to fill `limit` (bounded ~8s, complete). "
            "False reads on-screen rows only (fast).",
        ] = True,
        include_media: Annotated[
            bool,
            "True embeds image bubbles as data URIs for AI vision. "
            "False (default) keeps reads light: no files, no downloads.",
        ] = False,
    ) -> dict:
        """Read latest messages, opening a room first when `room` is given.

        Side-effect free: no files written. Only rows rendered on screen
        are visible (virtualized list), so deep history still returns
        just what the DOM holds.
        """
        try:
            with LineClient(quiet=True) as line:
                msgs = line.get_messages(
                    room=room,
                    limit=limit,
                    date=date,
                    date_from=date_from,
                    date_to=date_to,
                    sender=sender,
                    keyword=keyword,
                    scroll=scroll,
                    with_media=include_media,
                )
            return _ok([asdict(m) for m in msgs])
        except LineError as e:
            return _fail(e)

    @mcp.tool(
        title="Unread digest",
        tags={"line", "read"},
        annotations=READ_ONLY,
    )
    def unread_digest() -> dict:
        """Unread rooms with latest preview in one call.

        Cheapest "what is unread" check. For full message bodies use
        unread_full instead.
        """
        try:
            with LineClient(quiet=True) as line:
                return _ok(line.unread_digest())
        except LineError as e:
            return _fail(e)

    @mcp.tool(
        title="Unread messages",
        tags={"line", "read"},
        annotations=READ_ONLY,
        timeout=300.0,
    )
    async def unread_full(
        limit_per_room: Annotated[int, Field(ge=1, le=50)] = 20,
        date: Annotated[str | None, "Day as YYYY-MM-DD. None means today (local)."] = None,
        scroll: Annotated[
            bool,
            "True scrolls up to fill `limit_per_room` (bounded ~8s per room). "
            "False reads on-screen rows only (fast).",
        ] = True,
        ctx: Context | None = None,
    ) -> dict:
        """Unread rooms with their message bodies, one room at a time.

        Opens each unread room, so cost grows with unread count. Progress
        is reported per room.
        """
        from datetime import date as _date

        loop = asyncio.get_running_loop()
        day = date

        def _run() -> list[dict]:
            with LineClient(quiet=True) as line:
                target_day = day or _date.today().isoformat()
                rooms = line.list_rooms(unread_only=True)
                out = []
                for i, room in enumerate(rooms):
                    line.open_room(room)
                    msgs = line.get_messages(limit=limit_per_room, date=target_day, scroll=scroll)
                    out.append(
                        {
                            "room": asdict(room),
                            "messages": [asdict(m) for m in msgs],
                        }
                    )
                    if ctx is not None:
                        _notify(loop, ctx, i + 1, len(rooms))
                return out

        try:
            return _ok(await asyncio.to_thread(_run))
        except LineError as e:
            return _fail(e)

    @mcp.tool(
        title="Search LINE messages",
        tags={"line", "read"},
        annotations=READ_ONLY,
        timeout=300.0,
    )
    async def search_messages(
        keyword: Annotated[str, "Substring to search in message text."],
        rooms: Annotated[
            list[int | str],
            "REQUIRED scope: room index, data-mid, or name substring, at "
            "least one. Get candidates from list_rooms first. Unscoped "
            "search across all rooms is not offered: it costs seconds "
            "per room.",
        ],
        date_from: Annotated[str | None, "Range start as YYYY-MM-DD."] = None,
        date_to: Annotated[str | None, "Range end as YYYY-MM-DD."] = None,
        limit_per_room: Annotated[int, Field(ge=1, le=100)] = 20,
        scroll: Annotated[
            bool,
            "True scrolls up to fill `limit_per_room` (bounded ~8s per room). "
            "False reads on-screen rows only (fast).",
        ] = True,
        ctx: Context | None = None,
    ) -> dict:
        """Search a keyword inside the given rooms only.

        Returns only rooms with matches, plus error entries for refs
        that match no room (a bad ref never fails the whole search).
        Sequential, roughly seconds per room, with progress reported
        per room.
        """
        loop = asyncio.get_running_loop()

        def _run() -> list[dict]:
            with LineClient(quiet=True) as line:
                # One listing for all refs (not one per ref).
                targets, skipped = _resolve_targets(rooms, line.list_rooms())
                out = list(skipped)
                for i, room in enumerate(targets):
                    line.open_room(room)
                    msgs = line.get_messages(
                        limit=limit_per_room,
                        date_from=date_from,
                        date_to=date_to,
                        keyword=keyword,
                        scroll=scroll,
                    )
                    if msgs:
                        out.append(
                            {
                                "room": asdict(room),
                                "messages": [asdict(m) for m in msgs],
                            }
                        )
                    if ctx is not None:
                        _notify(loop, ctx, i + 1, len(targets))
                return out

        try:
            return _ok(await asyncio.to_thread(_run))
        except LineError as e:
            return _fail(e)
