"""The `logout` tool: end this device's session and delete its credentials.

Signing out is the one operation here that deletes something, so it lives in
its own module and says so in its annotations. It never touches the archive or
the media directory: ending access is not the same as erasing history.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from line_desktop_mcp.paths import session_path
from line_desktop_mcp.reader import LineReader
from line_desktop_mcp.tools.login import SESSION_LOCK

#: Deleting the credentials is reversible only by scanning a new QR code, so a
#: client should warn before calling this, even though no message is touched.
LOGOUT_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)

_NEXT = "Call login to scan a new QR code. The archive and images were kept."


def _remove_quietly(path: Path) -> None:
    """Best effort: the caller checks afterwards whether the file is really gone."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def run_logout(reader: LineReader) -> dict[str, Any]:
    """Revoke the session on LINE, then delete the local copy.

    The server copy can fail (offline, Node gone) and the local file is deleted
    anyway, because the token on disk is what protects the account. Nothing in
    the archive or the media directory is removed.
    """
    path = session_path()
    if not path.exists():
        return {
            "ok": True,
            "already_logged_out": True,
            "session_deleted": False,
            "remote": "skipped",
            "remote_error": None,
            "archive": "kept",
            "next": _NEXT,
        }

    if not SESSION_LOCK.acquire(blocking=False):
        raise ToolError(
            "A login is already running in another tool call. Wait for it to "
            "finish, then call logout again."
        )
    try:
        try:
            remote = reader.revoke()
        except Exception as exc:  # noqa: BLE001 - deleting the file matters more
            remote = {"remote": "failed", "error": f"{type(exc).__name__}: {exc}"}
        reader.shutdown()
        _remove_quietly(path)
        # A fallback QR from an earlier login is single-use and secret.
        _remove_quietly(path.parent / "qr.txt")
    finally:
        SESSION_LOCK.release()

    if path.exists():
        raise ToolError(
            f"The LINE session at {path} could not be deleted. Close whatever is "
            "using that file and call logout again."
        )

    return {
        "ok": True,
        "already_logged_out": False,
        "session_deleted": True,
        "remote": remote["remote"],
        "remote_error": remote["error"],
        "archive": "kept",
        "next": _NEXT,
    }


def register(mcp: FastMCP, reader: LineReader) -> None:
    """Register `logout`, the tool that ends the session this server holds."""

    @mcp.tool(
        name="logout",
        title="Log out of LINE",
        tags={"login"},
        annotations=LOGOUT_ANNOTATIONS,
    )
    def logout() -> dict[str, Any]:
        """Log this device out of LINE and delete the saved session.

        LINE is asked to invalidate the session first. If that request fails,
        the local file is deleted anyway, because the stored token is what
        matters. Your phone stays logged in: only this server's device is
        signed out, and the next `login` scans a fresh QR code.

        The archive and the image backups are kept, so nothing that was already
        read is lost. Every tool still needs a login before it can reach LINE
        again, because the session file is what they run on.
        """
        return run_logout(reader)
