"""LINE session tools (single responsibility: session lifecycle only).

Read tools live in tools/line_msg.py. This module owns the two session
calls: a redacted read-only probe and one destructive wipe. Envelope shape
(ok/data/error + next) matches the read tools so AI clients branch the
same way.
"""

from typing import Annotated, Any

from fastmcp import FastMCP
from line_ext_msg import LineError
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import reset, shared_client
from .line_msg import _fail, _ok

# Read-only probe: safe to call any time, never asks for confirmation.
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

# Destructive wipe: forces a fresh QR login, so clients must confirm first.
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)


def _needs_confirm() -> dict[str, Any]:
    """Safe refusal when confirm flag is missing (no side effects)."""
    return {
        "ok": False,
        "error": "NeedsConfirmation",
        "message": "clear_session wipes the LINE login and needs a fresh QR scan.",
        "next": (
            "Ask the user to confirm they want to log out this Chrome profile, "
            "then retry with confirm=true. Suggest line_status with "
            "timeout_sec=0 first: that check can never open a window, unlike "
            "probe_session."
        ),
    }


def register_session_tools(mcp: FastMCP) -> None:
    """Register probe_session (safe) and clear_session (destructive)."""

    @mcp.tool(
        title="Probe LINE session",
        tags={"line", "session", "read"},
        annotations=READ_ONLY,
    )
    def probe_session() -> dict:
        """Check login state and redacted storage without changing anything.

        Returns key names with type and length only, never secrets. Like every
        read tool here it waits for a QR when no session exists, so it is
        non-blocking only once Chrome is logged in. Use `line_status` with
        `timeout_sec=0` when you need a check that can never open a window.
        """
        try:
            with shared_client() as line:
                return _ok(line.probe_session())
        except LineError as e:
            return _fail(e)

    @mcp.tool(
        title="Clear LINE session",
        tags={"line", "session", "destructive"},
        annotations=DESTRUCTIVE,
    )
    def clear_session(
        confirm: Annotated[
            bool,
            Field(description="Must be true. Without it the tool refuses safely."),
        ] = False,
        backup: Annotated[
            bool,
            Field(description="Save a redacted probe to session/ before wiping."),
        ] = True,
    ) -> dict:
        """Wipe the LINE login only; extension install stays.

        Args:
            confirm: Safety gate. Pass true only after the user agrees.
            backup: Keep a redacted probe file before wiping (default true).

        Irreversible. The debug Chrome is stopped on purpose, because the
        extension keeps the session in memory, so the next call needs a
        fresh QR scan via line_status.
        """
        if not confirm:
            return _needs_confirm()
        try:
            with shared_client() as line:
                # Prime the page fail-fast first. logout() reaches for a ready
                # page, and with no session that would open the QR window and
                # wait for a human, which a logout must never do.
                try:
                    line.status(wait_for_login=False)
                except LineError:
                    pass
                # Upstream does a live clear, stops Chrome, wipes disk folders.
                summary = line.logout(backup=backup)
            return _ok(summary)
        except LineError as e:
            return _fail(e)
        finally:
            # logout stopped the debug Chrome, so the shared client is dead.
            reset()
