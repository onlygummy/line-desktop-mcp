"""MCP server instance (single responsibility: wiring only)."""

import logging
import os
import sys

from fastmcp import FastMCP

from .tools.line_msg import register_line_tools
from .tools.session import register_session_tools


def _configure_library() -> None:
    """Set the line-ext-msg env defaults this MCP relies on.

    Profile and port give the MCP its own Chrome instance: the library
    reuses whatever Chrome answers the debug port, regardless of profile,
    so a distinct port is what truly keeps the MCP off the line-ext-msg
    CLI instance (which defaults to port 9222 and the line-chrome-debug
    profile). The dialog title labels the QR login window and its header.
    setdefault keeps every value overridable.
    """
    os.environ.setdefault("LINE_EXT_MSG_PROFILE", r"%LOCALAPPDATA%\line-desktop-mcp")
    os.environ.setdefault("LINE_EXT_MSG_PORT", "9223")
    os.environ.setdefault("LINE_EXT_MSG_DIALOG_TITLE", "Line Desktop MCP")


def _configure_logging() -> None:
    """Mirror library progress on stderr when LINE_DESKTOP_MCP_LOG is set.

    Off by default so stdout stays clean for the stdio transport. Attaches
    to the documented `line_ext_msg` logger instead of importing a
    subpackage, which 2.0 keeps private and free to change.
    """
    level_name = os.environ.get("LINE_DESKTOP_MCP_LOG", "").upper()
    if not level_name:
        return
    logger = logging.getLogger("line_ext_msg")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.propagate = False


_configure_library()
_configure_logging()

# Shared server object imported by CLI and by `fastmcp run`.
mcp = FastMCP(
    "line-desktop-mcp",
    instructions=(
        "Reads your own LINE chats through a dedicated headless Chrome "
        "profile. Call line_status first: it installs the LINE extension if "
        "missing and waits for the QR login in one continuous call. If it "
        "reports LoginRequired there is no window open, so call it again with "
        "the defaults to put a QR in front of the user; timeout_sec=0 is the "
        "only non-blocking check. Read tools never write files. clear_session "
        "is the only destructive tool and needs confirm=true."
    ),
)

register_line_tools(mcp)
register_session_tools(mcp)
