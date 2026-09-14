"""LINE session tools (single responsibility: session lifecycle only).

Read tools live in tools/line_msg.py. This module owns the two session
calls added in line-ext-msg 1.2.0: a safe read-only probe and one
destructive wipe. Envelope shape (ok/data/error + next) matches the
read tools so AI clients branch the same way.
"""

from typing import Annotated, Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from line_ext_msg import LineClient, LineError

from .line_msg import _fail, _ok

# Read-only probe: safe to call any time, never asks for confirmation.
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, idempotentHint=True, openWorldHint=True
)

# Destructive wipe: forces a fresh QR login, so clients must confirm first.
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)


def _needs_confirm() -> dict[str, Any]:
    """Safe refusal when confirm flag is missing (no side effects)."""
    return {
        "ok": False,
        "error": "NeedsConfirmation",
        "message": "clear_session wipes the LINE login and needs a fresh QR scan.",
        "next": (
            "Ask the user to confirm they want to log out this Chrome profile, "
            "then retry with confirm=true. Suggest probe_session first to check "
            "logged_in state without wiping anything."
        ),
    }


def register_session_tools(mcp: FastMCP) -> None:
    """Register probe_session (safe) and clear_session (destructive)."""

    @mcp.tool(
        title="Probe LINE session",
        tags={"line", "session", "read"},
        annotations=READ_ONLY,
        timeout=120.0,
    )
    def probe_session() -> dict:
        """Check login state and redacted storage without changing anything.

        Call this before clear_session, or when login looks flaky.
        Returns key names with type and length only, never secrets.
        """
        try:
            with LineClient(quiet=True) as line:
                return _ok(line.probe_session())
        except LineError as e:
            return _fail(e)

    @mcp.tool(
        title="Clear LINE session",
        tags={"line", "session", "destructive"},
        annotations=DESTRUCTIVE,
        timeout=180.0,
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

        Irreversible: the next call needs a fresh QR scan via wait_login.
        """
        if not confirm:
            return _needs_confirm()
        try:
            with LineClient(quiet=True) as line:
                # Upstream does live clear, stops Chrome, wipes disk folders.
                summary = line.clear_session(backup=backup)
            return _ok(summary)
        except LineError as e:
            return _fail(e)
