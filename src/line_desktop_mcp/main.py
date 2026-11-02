"""MCP entry point: FastMCP wiring only.

Tool implementations live in `tools/`; this module only decides what the server
is called, what it tells the client up front, and which tools it exposes.
"""

from __future__ import annotations

from fastmcp import FastMCP

from line_desktop_mcp.tools.login import register_login_tool

mcp = FastMCP(
    "Line Desktop MCP",
    instructions=(
        "Logs in to your own LINE account by scanning a QR code. Call line_login "
        "first: it returns at once when the saved session still works, and only "
        "opens the QR window when it does not. The window appears on the machine "
        "running this server, so the scan has to happen there. Nothing else is "
        "exposed yet: this server never sends messages and never changes chats."
    ),
)

register_login_tool(mcp)


def main() -> None:
    """Run on stdio, the transport desktop MCP clients spawn."""
    mcp.run()


if __name__ == "__main__":
    main()
