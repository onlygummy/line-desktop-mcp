"""LINE message tools (single responsibility: wrap line-ext-msg as MCP tools).

Read tools share one LineClient for the server lifetime, so the login wait
happens once instead of on every call (see client.py). All calls use quiet
mode to keep stdout clean for stdio transport. Only rendered rows are visible
(virtualized list), and a scan that stopped on its time budget says so
through `partial` rather than letting a short list read as "no match".
"""

import asyncio
from dataclasses import asdict
from typing import Annotated, Any

import anyio
from fastmcp import Context, FastMCP
from line_ext_msg import (
    ChatsViewMissing,
    LineError,
    Room,
    RoomNotFound,
    ScanProgress,
)
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import reset, shared_client

# Shared hints: every tool here only reads, repeats safely, and reaches
# out to a local browser. Clients use these to skip confirmations.
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

# Follow-up guidance per error kind, so the model tells the user what to
# do next instead of just reporting failure. Looked up along the exception's
# MRO, so a subclass such as LoginTimeout inherits its parent's guidance
# until it gets an entry of its own.
_NEXT = {
    "ChromeNotReady": (
        "The debug Chrome could not start or be reached on the CDP port. Ask "
        "the user to check that Google Chrome is installed and not blocked, "
        "and that no other process is holding the port. LINE_EXT_MSG_PORT "
        "raises it if something else already owns it."
    ),
    "AttachFailed": (
        "Chrome answered the debug port but the CDP attach failed. Usually the "
        "profile is locked by a Chrome window that is still starting up. Ask "
        "the user to close leftover debug Chrome windows and retry; if it "
        "keeps failing, the profile under LINE_EXT_MSG_PROFILE is probably "
        "corrupt."
    ),
    "ExtensionMissing": (
        "The LINE extension install did not finish (its Chrome window was "
        "closed). Ask the user to retry and install the LINE extension "
        "from the Web Store page that opens; the call keeps polling and "
        "continues by itself as soon as the install completes."
    ),
    "AppNotReady": (
        "The LINE page did not render in time, or it landed on a view this "
        "library does not read. Retry once; if it repeats, ask the user to run "
        "`line-ext-msg --dump` and send session/dumps/line_dom.html upstream "
        "so the selectors can be retuned."
    ),
    # Never claims a window is open: 3.1 raises this in four situations, and
    # the only ones with a window involved are a window the user already
    # closed. An open window that nobody acted on raises LoginTimeout instead.
    "LoginRequired": (
        "No LINE session is stored in the debug Chrome, and no QR window is "
        "open. This call did not wait: either it was given timeout_sec=0, or "
        "an earlier window was closed before the QR was scanned. Call "
        "line_status again with its defaults, and ask the user to scan the QR "
        "that appears in the 'Line Desktop MCP' window on their screen, "
        "entering the PIN on their phone if LINE asks for it. Leave Chrome "
        "running: the session lives in that Chrome process, so closing it "
        "forces a new QR scan. Pass timeout_sec=0 only when you want a check "
        "that can never open a window."
    ),
    "LoginTimeout": (
        "A login was needed and nobody completed it in time. The 'Line Desktop "
        "MCP' QR window is on the user's screen: ask them to scan it with the "
        "LINE app and enter the PIN on their phone, then retry. Raise "
        "timeout_sec on line_status if they need longer, or lower it if the "
        "wait is too slow. Chrome must stay running or the next call needs a "
        "new QR."
    ),
    "QrDialogFailed": (
        "The QR window could not open on this machine. It needs an interactive "
        "desktop session, so the server cannot run headless or on a remote "
        "box. Ask the user to run line_status from a desktop session, or log "
        "in by hand in the debug Chrome window, then retry."
    ),
    "ChatsViewMissing": (
        "The LINE tab is not showing the chat list, so no room can be read. "
        "The server retries once by navigating back to the chats view; if "
        "this error still appears, it is a LINE UI change, not an empty "
        "account. Ask the user to run `line-ext-msg --dump` and send "
        "session/dumps/line_dom.html upstream so the selectors can be retuned."
    ),
    "RoomNotFound": (
        "Call list_rooms to show available rooms, then retry with a "
        "data-mid or exact name instead of an index."
    ),
    # Base class, reached only by an error with no entry of its own.
    "LineError": (
        "The LINE library failed without a specific cause. The message above "
        "carries the detail. Retry once, then call line_status with "
        "timeout_sec=0 to see whether Chrome is still usable without waiting "
        "on the user."
    ),
}

# Attached to any entry the library marked truncated, so a partial scan reads
# as incomplete rather than as a room that had nothing to say.
_PARTIAL_HINT = (
    "The backfill scroll hit its time budget before the oldest message, so "
    "older messages may be missing. Do not read a short or empty list as "
    "proof that nothing earlier exists. Retry with a larger limit, or ask the "
    "user to raise LINE_EXT_MSG_SEARCH_SCROLL_MS."
)


def _ok(data: Any) -> dict:
    """Success envelope (single shape so callers branch on `ok`)."""
    return {"ok": True, "data": data}


def _hint_for(error: LineError) -> str | None:
    """First hint along the error's MRO, so subclasses inherit guidance.

    Matching on the exact class name alone would leave every future subclass
    without a `next` field, which is how a typed failure turns into a dead end
    for the model reading it.
    """
    for cls in type(error).__mro__:
        hint = _NEXT.get(cls.__name__)
        if hint:
            return hint
    return None


def _fail(error: LineError) -> dict:
    """Domain failure envelope (lets the model branch on `error` kind)."""
    out = {"ok": False, "error": type(error).__name__, "message": str(error)}
    hint = _hint_for(error)
    if hint:
        out["next"] = hint
    return out


def _with_partial_hint(entry: dict) -> dict:
    """Mark a scan entry the library could not finish with a `next` hint."""
    if entry.get("truncated"):
        entry["next"] = _PARTIAL_HINT
    return entry


def _resolve_ref(ref: int | str, rooms: list[Room]) -> Room:
    """Match a room by index, data-mid, or name (public API only).

    Mirrors upstream 3.1 resolution: exact data-mid, then a case-insensitive
    substring, then containing every word, so multi-word and mixed-case room
    names resolve. Upstream's own resolver lives in a private subpackage, so
    the order is repeated here rather than imported.
    """
    if isinstance(ref, int):
        for room in rooms:
            if room.index == ref:
                return room
    else:
        for room in rooms:
            if room.id and room.id == ref:
                return room
        # The guard matters: "".split() is [], and all() over an empty sequence
        # is True, so a blank ref would otherwise match the first room.
        needle = ref.strip().lower()
        if needle:
            for room in rooms:
                if needle in room.name.lower():
                    return room
            for room in rooms:
                lowered = room.name.lower()
                if all(word in lowered for word in needle.split()):
                    return room
    raise RoomNotFound(ref, [r.name for r in rooms])


def _resolve_targets(refs: list[int | str], all_rooms: list[Room]) -> tuple[list[Room], list[dict]]:
    """Resolve room refs against one listing; bad refs become error entries.

    The caller lists rooms once and passes it in, so N refs cost one
    listing. Unresolvable refs never crash the search: each becomes an
    entry the AI can report and skip. The resolved rooms go to
    `search_all(rooms=...)` as Room objects, which the library passes
    straight through without re-resolving.
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


def _notify(
    loop: asyncio.AbstractEventLoop, ctx: Context | None, progress: int, total: int
) -> None:
    """Best-effort progress from a worker thread (never fails the tool)."""
    if ctx is None:
        return
    try:
        fut = asyncio.run_coroutine_threadsafe(
            ctx.report_progress(progress=progress, total=total), loop
        )
        fut.result(timeout=5)
    except Exception:
        pass


def _progress_reporter(loop: asyncio.AbstractEventLoop, ctx: Context | None) -> "Any":
    """Adapt the library's ScanProgress ticks to MCP progress notifications."""

    def report(tick: ScanProgress) -> None:
        _notify(loop, ctx, tick.index, tick.total)

    return report


def register_line_tools(mcp: FastMCP) -> None:
    """Register read-only LINE tools (no file writes, no media downloads)."""

    @mcp.tool(
        title="LINE status",
        tags={"line", "read"},
        annotations=READ_ONLY,
    )
    def line_status(
        timeout_sec: Annotated[
            int,
            Field(
                ge=0,
                le=1800,
                description="How long to wait for the QR to be scanned. "
                "0 is the only value that skips the QR attempt: it fails fast "
                "and never opens a window. Any other value, including 1, waits "
                "and opens one.",
            ),
        ] = 300,
    ) -> dict:
        """Check the 5 readiness steps, then wait for the QR login.

        Call this first. It runs the whole setup in one call: install the
        extension if missing, wait for the app, then show the QR window and
        wait until the user scans it. When already logged in it returns at
        once. On LoginRequired there is no window open, so call it again with
        the defaults to put one in front of the user. Pass timeout_sec=0 for a
        check that can never open a window. No tool-level timeout is set.
        """
        try:
            # status() always calls session.connect(), which starts a fresh
            # Playwright instance. Reusing a client that already holds one
            # leaks the old connection and the new sync_playwright().start()
            # call then fails. Reset so every call starts clean.
            reset()
            with shared_client() as line:
                # wait_for_login is deliberately not passed: the library waits
                # by default, and timeout_sec=0 is the documented way to skip.
                steps = line.status(login_timeout_ms=timeout_sec * 1000)
            return _ok([asdict(s) for s in steps])
        except LineError as e:
            return _fail(e)

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
        index values shift when rooms reorder. If not logged in yet this
        waits for the QR, same as line_status.
        """
        try:
            with shared_client() as line:
                try:
                    rooms = line.list_rooms(unread_only=unread_only, query=query)
                except ChatsViewMissing:
                    # A previous tool call left the page on a room view.
                    # status() ends with ensure_chats_view(), so one call
                    # navigates back.
                    line.status()
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
        time_from: Annotated[str | None, "Range start time as HH:MM."] = None,
        time_to: Annotated[str | None, "Range end time as HH:MM."] = None,
        sender: Annotated[str | None, "Substring filter on sender name."] = None,
        keyword: Annotated[str | None, "Substring filter on message text."] = None,
        scroll: Annotated[
            bool,
            "True scrolls up to fill `limit`. False reads on-screen rows only "
            "(fast, and will usually under-report).",
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

        Returns `{messages, scroll_stop, partial}`. Check `partial`: when it
        is true the scroll budget ran out before the oldest message, so the
        list is short because the scan stopped, not because the room was
        quiet.
        """
        try:
            with shared_client() as line:

                def _fetch():
                    return line.get_messages(
                        room=room,
                        limit=limit,
                        date=date,
                        date_from=date_from,
                        date_to=date_to,
                        time_from=time_from,
                        time_to=time_to,
                        sender=sender,
                        keyword=keyword,
                        scroll=scroll,
                        with_media=include_media,
                    )

                try:
                    msgs = _fetch()
                except ChatsViewMissing:
                    # Resolving `room` calls list_rooms() internally, which
                    # fails when a previous call left the page on a room view.
                    line.status()
                    msgs = _fetch()
            partial = msgs.scroll_stop == "budget"
            data: dict[str, Any] = {
                "messages": [asdict(m) for m in msgs],
                "scroll_stop": msgs.scroll_stop,
                "partial": partial,
            }
            if partial:
                data["next"] = _PARTIAL_HINT
            return _ok(data)
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
            with shared_client() as line:
                try:
                    return _ok(line.unread_digest())
                except ChatsViewMissing:
                    line.status()
                    return _ok(line.unread_digest())
        except LineError as e:
            return _fail(e)

    @mcp.tool(
        title="Unread messages",
        tags={"line", "read"},
        annotations=READ_ONLY,
    )
    async def unread_full(
        limit_per_room: Annotated[int, Field(ge=1, le=50)] = 20,
        date: Annotated[str | None, "Day as YYYY-MM-DD. None means today (local)."] = None,
        ctx: Context | None = None,
    ) -> dict:
        """Unread rooms with their message bodies, one room at a time.

        Opens each unread room, so cost grows with unread count. Progress is
        reported per room.

        A room can come back with no messages while still showing unread,
        because the unread ones predate `date`. Compare `room.unread` with
        the message count before reporting "nothing pending".
        """
        loop = asyncio.get_running_loop()
        report = _progress_reporter(loop, ctx)

        def _run() -> list[dict]:
            with shared_client() as line:
                try:
                    entries = line.unread_full(
                        date=date,
                        limit_per_room=limit_per_room,
                        on_progress=report,
                    )
                except ChatsViewMissing:
                    # A previous tool call left the page on a room view.
                    # status() ends with ensure_chats_view(), so one call
                    # navigates back. A second failure is a real UI change.
                    line.status()
                    entries = line.unread_full(
                        date=date,
                        limit_per_room=limit_per_room,
                        on_progress=report,
                    )
            return [_with_partial_hint(e) for e in entries]

        try:
            return _ok(await anyio.to_thread.run_sync(_run))
        except LineError as e:
            return _fail(e)

    @mcp.tool(
        title="Search LINE messages",
        tags={"line", "read"},
        annotations=READ_ONLY,
    )
    async def search_messages(
        keyword: Annotated[str, "Substring to search in message text."],
        rooms: Annotated[
            list[int | str],
            "REQUIRED scope: room index, data-mid, or name substring, at "
            "least one. Get candidates from list_rooms first. Unscoped "
            "search across all rooms is not offered: it costs a minute per "
            "room because a filtered scan scrolls until its budget runs out.",
        ],
        date_from: Annotated[str | None, "Range start as YYYY-MM-DD."] = None,
        date_to: Annotated[str | None, "Range end as YYYY-MM-DD."] = None,
        limit_per_room: Annotated[int, Field(ge=1, le=100)] = 20,
        ctx: Context | None = None,
    ) -> dict:
        """Search a keyword inside the given rooms only.

        Returns only rooms with matches, plus error entries for refs that
        match no room (a bad ref never fails the whole search). Sequential,
        roughly a minute per room, with progress reported per room.

        A room with `truncated: true` was not scanned to the end, so it may
        match further back. It is listed even when it matched nothing, and
        its `next` field says so: an empty list there means "not reached",
        not "not there".
        """
        loop = asyncio.get_running_loop()
        report = _progress_reporter(loop, ctx)

        def _run() -> list[dict]:
            with shared_client() as line:
                # Resolve first so a bad ref is reported rather than dropped.
                # search_all re-reads the room list itself, which is one extra
                # pass, in exchange for the per-room scroll fix upstream.
                # Both list_rooms() calls can hit a page left on a room view
                # by a previous tool call, so retry once after status().
                try:
                    all_rooms = line.list_rooms()
                except ChatsViewMissing:
                    line.status()
                    all_rooms = line.list_rooms()
                targets, skipped = _resolve_targets(rooms, all_rooms)
                # Annotated as the union because list is invariant: passing
                # list[Room] straight through fails type checking.
                scope: list[int | str | Room] = list(targets)
                try:
                    entries = line.search_all(
                        keyword,
                        date_from=date_from,
                        date_to=date_to,
                        rooms=scope,
                        limit_per_room=limit_per_room,
                        on_progress=report,
                    )
                except ChatsViewMissing:
                    line.status()
                    entries = line.search_all(
                        keyword,
                        date_from=date_from,
                        date_to=date_to,
                        rooms=scope,
                        limit_per_room=limit_per_room,
                        on_progress=report,
                    )
            return [*skipped, *(_with_partial_hint(e) for e in entries)]

        try:
            return _ok(await anyio.to_thread.run_sync(_run))
        except LineError as e:
            return _fail(e)
