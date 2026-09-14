# line-desktop-mcp

> Unofficial community project. Not affiliated with, endorsed by, or supported by LINE Corporation or LY Corporation. Uses your own logged-in account through the LINE Chrome Extension. Read tools are side-effect free; `clear_session` is the only destructive tool and needs explicit confirmation.

FastMCP server for LINE Desktop automation. Wraps `line-ext-msg>=1.2` (LINE Chrome Extension reader) as MCP tools.

## LINE tools

Read tools are side-effect free (no files written, no media downloaded). Every result uses an `ok`/`data`/`error` envelope; failures carry a `next` field telling the AI what to ask the user to do. Session tools live in their own domain: `probe_session` is safe to call any time, `clear_session` wipes the login and always needs a fresh QR scan afterwards.

| Tool | Description |
|------|-------------|
| `line_status` | 5 readiness checks (Chrome, attach, extension, page, login) |
| `wait_login` | Open Chrome and wait up to 3 min for the user to log in |
| `list_rooms` | Chat rooms, optional `unread_only` / `query` filter |
| `get_messages` | Latest messages, optional room ref + date/sender/keyword filters, `include_media` embeds image data URIs |
| `unread_digest` | Unread rooms with latest preview in one call |
| `unread_full` | Unread rooms with message bodies, with per-room progress |
| `search_messages` | Keyword search inside REQUIRED `rooms` scope, with per-room progress |
| `probe_session` | Login state + redacted storage check, safe any time, no side effects |
| `clear_session` | Wipe LINE login only (destructive, needs `confirm=true`), next run needs QR scan |

First run opens a separate Chrome profile: call `line_status`, log in with QR/email in the window that opens, then call `wait_login`. Chrome runs headless by default and pops a visible window only for the QR scan; the login normally survives restarts. Only `clear_session` forces a fresh login every time. Room ref accepts index, data-mid, or name substring (prefer data-mid or name: index shifts when rooms reorder). Dates use YYYY-MM-DD. `search_messages` never scans all rooms: pass candidates from `list_rooms` first. Message reads backfill by scrolling up to `limit` (bounded ~8s); pass `scroll=false` for fast on-screen-only reads. When login looks flaky, call `probe_session` first; call `clear_session` with `confirm=true` only after the user agrees to log out. `wait_login` accepts up to 600 seconds and always covers it without cutting off early. After a QR login it settles briefly and proves headless reuse inside the same call, so `ok:true` means later tools can call right away with no manual waiting. `search_messages` skips refs that match no room (returned as `RoomNotFound` entries) instead of failing the whole call. Image bubbles are text-only by default; pass `include_media=true` to `get_messages` when the AI needs to see pictures.

## Tests

```bash
uv run python -m unittest discover -s tests -v
```

Browser-free unit tests only (room resolving, envelopes, login wait, session gate, CLI defaults). No Chrome needed.

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
uvx --from ./dist/line_desktop_mcp-1.1.0-py3-none-any.whl line-desktop-mcp --help
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
  tools/session.py  # Session tools (probe_session read-only, clear_session destructive)
```

Add a new tool domain by creating `tools/<domain>.py` with a `register_<domain>_tools(mcp)` function, then calling it from `server.py`.

## Publish

```bash
uv build
uv publish
```

Requires `requires-python = ">=3.10"` and the `line-desktop-mcp` console script defined in `pyproject.toml`.
