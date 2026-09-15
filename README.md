# line-desktop-mcp

> Unofficial community project. Not affiliated with, endorsed by, or supported by LINE Corporation or LY Corporation. It reads your own logged-in account through the LINE Chrome Extension. Read tools are side-effect free; `clear_session` is the only destructive tool and needs explicit confirmation.

An MCP server that lets an AI client read your LINE chats. It wraps `line-ext-msg>=2.0,<3`, drives a dedicated headless Chrome over CDP, and exposes LINE as MCP tools.

## Requirements

- Windows (Chrome discovery and process control use Windows paths and PowerShell)
- Google Chrome installed
- A LINE account you can log into with a QR scan
- `uv` installed, which provides `uvx`

You do not install the LINE Chrome Extension yourself. The first run opens the Web Store page and waits while you install it.

## Quick start

### 1. Add the server to your MCP client

Claude Desktop:

```json
{
  "mcpServers": {
    "line-desktop": {
      "command": "uvx",
      "args": ["line-desktop-mcp@latest"]
    }
  }
}
```

opencode:

```json
{
  "mcp": {
    "line-desktop-mcp": {
      "type": "local",
      "command": ["uvx", "line-desktop-mcp@latest"]
    }
  }
}
```

`@latest` asks uvx for the newest published version and refreshes its cache on every launch, so you keep getting updates without editing the config. If you prefer a fixed version for reproducibility, use `line-desktop-mcp@2.0.0` instead.

### 2. First run

Ask your client to call `line_status` with its defaults. That one call runs the whole setup:

1. starts a dedicated headless Chrome profile,
2. installs the LINE extension if it is missing (it opens the Web Store and keeps polling until the install finishes),
3. waits for the app to render,
4. shows the QR in a small `Line Desktop MCP` dialog.

Scan the QR with your phone and enter the PIN if LINE asks for it. When you are already logged in, step 4 is skipped and the call returns at once.

Chrome is left running on purpose. The LINE session lives in that Chrome process, not on disk, so closing Chrome or rebooting means scanning again. Only `clear_session` logs out on purpose.

### 3. Everyday use

Once setup is done, just ask your AI client. Things that work well:

- "Which LINE chats have unread messages?"
- "Read the last messages in the family chat."
- "Search the Work room for 'invoice' since 2026-09-01."
- "Show me the image someone posted yesterday" (ask for `include_media=true`).

## Tools

Read tools never write files and never download media. Every result uses an `ok` / `data` / `error` envelope, and a failure carries a `next` field that tells the AI what to ask you to do.

| Tool | What it does |
|------|--------------|
| `line_status` | Checks the five readiness steps. With `wait_for_login=true` (the default) it also installs the extension if missing and waits for the QR login, all in one continuous call. Pass `wait_for_login=false` for a quick check that fails fast. |
| `list_rooms` | Lists chat rooms with unread counts and a preview. Optional `unread_only` and `query` filters. |
| `get_messages` | Reads the latest messages, optionally opening a room first. Filters by date, range, sender, or keyword. `include_media=true` embeds image bubbles as data URIs for the AI to see. |
| `unread_digest` | Unread rooms with the latest preview. Cheapest way to answer "what is unread". |
| `unread_full` | Unread rooms with their message bodies, one room at a time, with per-room progress. |
| `search_messages` | Searches a keyword inside the rooms you pass. The scope is required on purpose: scanning every room costs seconds per room. |
| `probe_session` | Reports login state plus a redacted storage probe. Safe to call any time, no side effects. |
| `clear_session` | Wipes the LINE login only, keeping the extension. Destructive, needs `confirm=true`, and the next call needs a new QR scan. |

## How it works

- **Room references.** A room can be named by index, data-mid, or a substring of its name. Prefer data-mid or the name: index values shift when rooms reorder.
- **Dates.** Use `YYYY-MM-DD`. `get_messages` takes a single `date` or a `date_from` / `date_to` range.
- **Scroll.** Reads backfill by scrolling up to `limit`, bounded to about 8 seconds. Pass `scroll=false` to read only what is on screen, which is fast.
- **Media.** Image bubbles are text only by default. `include_media=true` adds a data URI in memory, and nothing is written to disk.
- **Search scope.** `search_messages` only scans the rooms you pass, and a bad reference becomes a `RoomNotFound` entry instead of failing the whole call.
- **Rendered rows only.** LINE uses a virtualized list, so a deep history still returns only what the DOM holds, even for a wide date range.
- **Sessions.** The session belongs to the running Chrome process. Keep Chrome open to avoid scanning again.

## Configuration

All settings are environment variables. The MCP sets the first three defaults before the library loads, and `setdefault` means your own values win.

| Variable | Default | Purpose |
|----------|---------|---------|
| `LINE_EXT_MSG_PROFILE` | `%LOCALAPPDATA%\line-desktop-mcp` | Chrome profile used by the server |
| `LINE_EXT_MSG_PORT` | `9223` | CDP debug port. A separate port keeps the MCP off the `line-ext-msg` CLI, which uses `9222`. The library reuses whatever Chrome answers a debug port, regardless of profile, so the port is what actually isolates the two. |
| `LINE_EXT_MSG_DIALOG_TITLE` | `Line Desktop MCP` | Title of the QR login dialog and its header |
| `LINE_DESKTOP_MCP_LOG` | unset | Set to `INFO` or `DEBUG` to mirror library progress on stderr |

The library writes a few runtime files under `session/` in the server working directory: `qr.png` and `qr_status.json` during login, `chrome.pid` for the debug Chrome, and a redacted probe before `clear_session`. The folder is git-ignored.

## Privacy and safety

- The server reads your own account. It does not use any LINE API and does not send your data anywhere except back to your MCP client.
- `probe_session` returns key names with type and length only, never secret values.
- `clear_session` is the only destructive tool. Without `confirm=true` it refuses safely and changes nothing.
- Nothing is saved unless you ask for it through your client; read tools leave no files behind.

## Troubleshooting

- **Login looks flaky.** Call `probe_session` first to check `logged_in` without changing anything.
- **The extension is missing.** The first call opens the Web Store page and keeps polling until the install finishes, then continues on its own.
- **Rooms cannot be read after a LINE update.** The selectors live in `line-ext-msg`. Dump the DOM and send it upstream to tune them.
- **The first call fails with a client-side timeout.** A cold Chrome start can take longer than your MCP client's default request timeout. Retry once Chrome is warm, or raise the server `timeout` in your client config.

## For developers

```bash
uv sync
uv run line-desktop-mcp --help
uv run line-desktop-mcp
```

Run with HTTP transport for remote access:

```bash
uv run line-desktop-mcp --transport http --port 8000
```

Run via the FastMCP CLI:

```bash
uv run fastmcp run src/line_desktop_mcp/server.py:mcp
```

Test a local wheel before release:

```bash
uv build
uvx --from ./dist/line_desktop_mcp-2.0.0-py3-none-any.whl line-desktop-mcp --help
```

### Tests

```bash
uv run python -m unittest discover -s tests -v
```

Browser-free unit tests only (room resolving, envelopes, login status, session gate, env defaults, CLI defaults). No Chrome needed.

Lint and type check:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

CI runs all three plus the tests on Python 3.10 to 3.14.

### Structure

```text
src/line_desktop_mcp/
  server.py         # FastMCP wiring, instructions, and env defaults
  cli.py            # argparse + mcp.run(), installed as `line-desktop-mcp`
  __main__.py       # `python -m line_desktop_mcp`
  tools/line_msg.py # LINE tools (status, rooms, messages, search)
  tools/session.py  # Session tools (probe_session read-only, clear_session destructive)
```

Add a new tool domain by creating `tools/<domain>.py` with a `register_<domain>_tools(mcp)` function, then calling it from `server.py`.

### Publish

```bash
uv build
uv publish
```

Requires `requires-python = ">=3.10"` and the `line-desktop-mcp` console script defined in `pyproject.toml`.
