"""LINE message tools (single responsibility: wrap line-ext-msg as MCP tools).

Each tool opens its own LineClient and closes it on return, so no Chrome
process or login session leaks between calls. All calls use quiet mode to
keep stdout clean for stdio transport. Only rendered rows are visible
(virtualized list), so wide ranges still return just what the DOM holds.
"""

import asyncio
import time
from dataclasses import asdict
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from line_ext_msg import (
    ExtensionMissing,
    LineClient,
    LineError,
    LoginRequired,
    Room,
    RoomNotFound,
)

# Shared hints: every tool here only reads, repeats safely, and reaches
# out to a local browser. Clients use these to skip confirmations.
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, idempotentHint=True, openWorldHint=True
)

# Follow-up guidance per error kind, so the model tells the user what to
# do next instead of just reporting failure.
_NEXT = {
    "LoginRequired": (
        "Ask the user to log in with QR or email in the Chrome window "
        "that opened, then call wait_login (waits up to 3 minutes). "
        "Keep that Chrome window open: closing it always requires "
        "logging in again."
    ),
    "ExtensionMissing": (
        "Ask the user to install the LINE extension from the store page "
        "that was opened, then retry the same call."
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


def _notify(
    loop: asyncio.AbstractEventLoop, ctx: Context, progress: int, total: int
) -> None:
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
        timeout=120.0,
    )
    def line_status() -> dict:
        """Check the 5 readiness steps: Chrome, attach, extension, page, login.

        Call this first when unsure the setup works. On failure the result
        carries a `next` field telling the user what to do.
        """
        try:
            with LineClient(quiet=True) as line:
                steps = line.status()
            return _ok([asdict(s) for s in steps])
        except LineError as e:
            return _fail(e)

    @mcp.tool(
        title="Wait for LINE login",
        tags={"line", "read"},
        annotations=READ_ONLY,
        timeout=200.0,
    )
    def wait_login(
        timeout_sec: Annotated[int, Field(ge=10, le=600)] = 180,
    ) -> dict:
        """Open Chrome and wait until the user finishes LINE login.

        Args:
            timeout_sec: Max seconds to wait (default 180). A Chrome window
                with the LINE login screen opens on first use; the user logs
                in there once with QR or email and it persists afterwards.
        """
        deadline = time.monotonic() + timeout_sec
        while True:
            try:
                # Fresh client per attempt: public API only, always closed.
                with LineClient(quiet=True) as line:
                    steps = line.status()
                return _ok(
                    {
                        "logged_in": True,
                        "steps": [asdict(s) for s in steps],
                    }
                )
            except LoginRequired:
                pass  # Window is open; keep waiting for the user.
            except ExtensionMissing:
                pass  # Store page is open; user may still install it.
            except LineError as e:
                # Chrome/attach/page faults never resolve by waiting.
                return _fail(e)
            if time.monotonic() >= deadline:
                break
            time.sleep(3)
        out = _fail(LoginRequired("ยังไม่ล็อกอิน LINE"))
        return out

    @mcp.tool(
        title="List LINE rooms",
        tags={"line", "read"},
        annotations=READ_ONLY,
        timeout=120.0,
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
        timeout=120.0,
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
    ) -> dict:
        """Read latest messages, opening a room first when `room` is given.

        Side-effect free: no files written, no media downloaded. Only rows
        rendered on screen are visible (virtualized list), so deep history
        still returns just what the DOM holds.
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
                )
            return _ok([asdict(m) for m in msgs])
        except LineError as e:
            return _fail(e)

    @mcp.tool(
        title="Unread digest",
        tags={"line", "read"},
        annotations=READ_ONLY,
        timeout=120.0,
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
        date: Annotated[
            str | None, "Day as YYYY-MM-DD. None means today (local)."
        ] = None,
        scroll: Annotated[
            bool,
            "True scrolls up to fill `limit_per_room` (bounded ~8s per room). "
            "False reads on-screen rows only (fast).",
        ] = True,
        ctx: Context = None,  # type: ignore[assignment]
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
                    msgs = line.get_messages(
                        limit=limit_per_room, date=target_day, scroll=scroll
                    )
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
        ctx: Context = None,  # type: ignore[assignment]
    ) -> dict:
        """Search a keyword inside the given rooms only.

        Returns only rooms with matches. Sequential, roughly seconds per
        room, with progress reported per room.
        """
        loop = asyncio.get_running_loop()

        def _run() -> list[dict]:
            with LineClient(quiet=True) as line:
                targets = [_resolve_ref(ref, line.list_rooms()) for ref in rooms]
                out = []
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
