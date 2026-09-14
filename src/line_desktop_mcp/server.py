"""MCP server instance (single responsibility: wiring only)."""

from fastmcp import FastMCP

from .tools.line_msg import register_line_tools
from .tools.session import register_session_tools

# Shared server object imported by CLI and by `fastmcp run`.
mcp = FastMCP("line-desktop-mcp")

register_line_tools(mcp)
register_session_tools(mcp)
