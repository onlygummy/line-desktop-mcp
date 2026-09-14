# line-desktop-mcp

> Unofficial community project. Not affiliated with, endorsed by, or supported by LINE Corporation or LY Corporation. Uses your own logged-in account through the LINE Chrome Extension in read-only mode.

FastMCP server for LINE Desktop automation. Wraps `line-ext-msg` (read-only LINE Chrome Extension reader) as MCP tools.

## LINE tools

All read-only, side-effect free (no files written, no media downloaded). Every result uses an `ok`/`data`/`error` envelope; failures carry a `next` field telling the AI what to ask the user to do.

| Tool | Description |
|------|-------------|
| `line_status` | 5 readiness checks (Chrome, attach, extension, page, login) |
| `wait_login` | Open Chrome and wait up to 3 min for the user to log in |
| `list_rooms` | Chat rooms, optional `unread_only` / `query` filter |
| `get_messages` | Latest messages, optional room ref + date/sender/keyword filters |
| `unread_digest` | Unread rooms with latest preview in one call |
| `unread_full` | Unread rooms with message bodies, with per-room progress |
| `search_messages` | Keyword search inside REQUIRED `rooms` scope, with per-room progress |

First run opens a separate Chrome profile: call `line_status`, log in with QR/email in the window that opens, then call `wait_login`. Keep that Chrome window open afterwards: closing it always requires logging in again (extension token is restart-scoped). Room ref accepts index, data-mid, or name substring (prefer data-mid or name: index shifts when rooms reorder). Dates use YYYY-MM-DD. `search_messages` never scans all rooms: pass candidates from `list_rooms` first. Message reads backfill by scrolling up to `limit` (bounded ~8s); pass `scroll=false` for fast on-screen-only reads.

## Run (dev)

```bash
uv sync
uv run line-desktop-mcp --help
uv run line-desktop-mcp
```

Run with HTTP transport for remote access:

```bash
uv run line-desktop-mcp --transport http --port 8000
```

Run via FastMCP CLI:

```bash
uv run fastmcp run src/line_desktop_mcp/server.py:mcp
```

## Use without cloning (after PyPI release)

```bash
uvx line-desktop-mcp --help
pipx install line-desktop-mcp
pipx run line-desktop-mcp --help
```

Test a local wheel before release:

```bash
uv build
uvx --from ./dist/line_desktop_mcp-1.0.0-py3-none-any.whl line-desktop-mcp --help
```

## Claude Desktop config

```json
{
  "mcpServers": {
    "line-desktop": {
      "command": "uvx",
      "args": ["line-desktop-mcp"]
    }
  }
}
```

For a local checkout use `uv`:

```json
{
  "mcpServers": {
    "line-desktop": {
      "command": "uv",
      "args": ["--directory", "D:/path/to/line-desktop-mcp", "run", "line-desktop-mcp"]
    }
  }
}
```

## Structure

```text
src/line_desktop_mcp/
  server.py        # FastMCP("line-desktop-mcp") wiring only
  cli.py           # argparse + mcp.run(), installed as `line-desktop-mcp`
  tools/line_msg.py # LINE tools (status, login wait, rooms, messages, search)
```

Add a new tool domain by creating `tools/<domain>.py` with a `register_<domain>_tools(mcp)` function, then calling it from `server.py`.

## Publish

```bash
uv build
uv publish
```

Requires `requires-python = ">=3.10"` and the `line-desktop-mcp` console script defined in `pyproject.toml`.
