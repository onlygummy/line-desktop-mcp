"""MCP entry point: FastMCP wiring only.

Tool implementations live in `tools/`. This module decides what the server is
called, what it tells the client up front, and which shared objects the tools
borrow: one LINE reader (a single client and lock) and one SQLite archive.
"""

from __future__ import annotations

import atexit

from fastmcp import FastMCP

from line_desktop_mcp.archive import Archive
from line_desktop_mcp.reader import LineReader
from line_desktop_mcp.tools import get_messages, list_chats, login, search_messages

archive = Archive()
reader = LineReader()

mcp = FastMCP(
    "Line Desktop MCP",
    instructions=(
        "Reads your own LINE chats. Call login first: it returns at once when the "
        "saved session still works, and only opens a QR window when it does not. "
        "Then use list_chats to find the chat and get_messages to read it, or "
        "search_messages to look for text across what has already been read. Every "
        "message read is stored in a local SQLite archive and every image is "
        "saved as a backup; nothing is sent to LINE and nothing is marked as read."
    ),
)

login.register(mcp)
list_chats.register(mcp, reader, archive)
get_messages.register(mcp, reader, archive)
search_messages.register(mcp, reader, archive)


def _shutdown() -> None:
    """Release the Node signing subprocess and the database on the way out."""
    reader.close()
    archive.close()


atexit.register(_shutdown)


def main() -> None:
    """Run on stdio, the transport desktop MCP clients spawn."""
    mcp.run()


if __name__ == "__main__":
    main()
