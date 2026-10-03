# line-desktop-mcp

> Unofficial community project. Not affiliated with, endorsed by, or supported by LINE Corporation or LY Corporation. It reads your own logged-in account through the LINE Chrome Extension. Read tools are side-effect free; `clear_session` is the only destructive tool and needs explicit confirmation.

An MCP server that lets an AI client read your LINE chats. It wraps `line-ext-msg>=3.1,<4`, drives a dedicated headless Chrome over CDP, and exposes LINE as MCP tools.

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

`@latest` asks uvx for the newest published version and refreshes its cache on every launch, so you keep getting updates without editing the config. If you prefer a fixed version for reproducibility, use `line-desktop-mcp@3.0.0` instead.

### 2. First run

Ask your client to call `line_status` with its defaults. That one call runs the whole setup:

1. starts a dedicated headless Chrome profile,
2. installs the LINE extension if it is missing (it opens the Web Store and keeps polling until the install finishes),
3. waits for the app to render,
4. shows the QR in a small `Line Desktop MCP` window.

Scan the QR with your phone and enter the PIN if LINE asks for it. The call waits up to `timeout_sec` (300 by default) for that, then reports `LoginTimeout`. When you are already logged in, step 4 is skipped and the call returns at once.

`LoginRequired` means no session and no window open: the call either ran with `timeout_sec=0`, or an earlier window was closed before the scan. Call `line_status` again with its defaults to put a QR in front of the user.

Chrome is left running on purpose. The LINE session lives in that Chrome process, not on disk, so closing Chrome or rebooting means scanning again. Only `clear_session` logs out on purpose.

Every tool waits for a QR the same way, so whichever one the AI calls first will open the login window if no session exists. Once one call succeeds, the rest reuse it without waiting again. `line_status` resets the Chrome connection before running the readiness checks, so each call starts with a fresh Playwright instance.

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
| `line_status` | Checks the five readiness steps, then installs the LINE extension if missing and waits for the QR login, all in one continuous call, up to `timeout_sec` (default 300). Pass `timeout_sec=0` for a check that fails fast and never opens a window; it is the only value that skips the QR attempt. Resets the Chrome connection before running, so each call starts clean. |
| `list_rooms` | Lists chat rooms with unread counts and a preview. Optional `unread_only` and `query` filters. |
| `get_messages` | Reads the latest messages, optionally opening a room first. Filters by date, date range, time range, sender, or keyword. `include_media=true` embeds image bubbles as data URIs for the AI to see. Returns `{messages, scroll_stop, partial}`. |
| `unread_digest` | Unread rooms with the latest preview. Cheapest way to answer "what is unread". |
| `unread_full` | Unread rooms with their message bodies, one room at a time, with per-room progress. |
| `search_messages` | Searches a keyword inside the rooms you pass. The scope is required on purpose: a filtered scan costs up to a minute per room. |
| `probe_session` | Reports login state plus a redacted storage probe. No side effects, but it waits for a QR like every other read tool when no session exists. |
| `clear_session` | Wipes the LINE login only, keeping the extension. Destructive, needs `confirm=true`, and the next call needs a new QR scan. |

## How it works

- **Room references.** A room can be named by index, data-mid, or part of its name. Names match case-insensitively, and a multi-word fragment matches a room that contains every word. Prefer data-mid or the name: index values shift when rooms reorder.
- **Dates and times.** Use `YYYY-MM-DD` for dates and `HH:MM` for times. `get_messages` takes a single `date`, a `date_from` / `date_to` range, or a `time_from` / `time_to` window.
- **Scroll budgets.** Reads scroll up to `limit`. A *filtered* read has no target row count, so it scrolls until the oldest message, the date boundary, or its budget, whichever comes first. That budget is `max(SEARCH_SCROLL_MS, min(limit * 1000ms, SCROLL_CAP_MS))`, so roughly a minute per room by default. `scroll=false` reads only on-screen rows, which is fast but will usually under-report on a filtered query.
- **Partial results.** When a scan stops on its budget it says so instead of returning an empty list. `get_messages` sets `partial: true`, and `unread_full` / `search_messages` mark the affected rooms `truncated: true`. A truncated room is listed even when it matched nothing, so an empty list there means "not reached", not "not there".
- **Media.** Image bubbles are text only by default. `include_media=true` adds a data URI in memory, and nothing is written to disk.
- **Search scope.** `search_messages` only scans the rooms you pass, and a bad reference becomes a `RoomNotFound` entry instead of failing the whole call.
- **Rendered rows only.** LINE uses a virtualized list, so a deep history still returns only what the DOM holds, even for a wide date range.
- **Sessions.** The session belongs to the running Chrome process. The server keeps one Chrome connection for its whole life, so the readiness check and the login wait happen once. Keep Chrome open to avoid scanning again. Calls are serialised, because every tool drives the same browser tab.
- **Wrong view recovery.** After a tool opens a specific room (for example `get_messages` with a `room` parameter), the browser tab stays on that room. The next tool that needs the chat list will retry once by navigating back to the chats view. If it still fails, that is a LINE UI change, not a stale page.
- **One login window.** The QR is shown in a Tk window on the machine running the server. That needs an interactive desktop session, so logging in over `--transport http` from another machine does not work.

## Configuration

All settings are environment variables. The MCP sets the first three defaults before the library loads, and `setdefault` means your own values win.

| Variable | Default | Purpose |
|----------|---------|---------|
| `LINE_EXT_MSG_PROFILE` | `%LOCALAPPDATA%\line-desktop-mcp` | Chrome profile used by the server |
| `LINE_EXT_MSG_PORT` | `9223` | CDP debug port. A separate port keeps the MCP off the `line-ext-msg` CLI, which uses `9222`. The library reuses whatever Chrome answers a debug port, regardless of profile, so the port is what actually isolates the two. |
| `LINE_EXT_MSG_DIALOG_TITLE` | `Line Desktop MCP` | Title of the QR login window and its header |
| `LINE_EXT_MSG_SEARCH_SCROLL_MS` | `60000` | Lower bound on the per-room scroll budget when a filter is set. Raise it when `search_messages` or `unread_full` keeps reporting `truncated`. |
| `LINE_EXT_MSG_SCROLL_CAP_MS` | `300000` | Ceiling on what `limit_per_room` can raise the budget to. `0` removes the ceiling. |
| `LINE_EXT_MSG_LOGIN_WAIT_MS` | `300000` | How long a read tool waits for a QR when it finds no session. `line_status` overrides this with its own `timeout_sec`; the other read tools have no per-call timeout, so this is their knob. |
| `LINE_DESKTOP_MCP_LOG` | unset | Set to `INFO` or `DEBUG` to mirror library progress on stderr |

The library writes a few runtime files under `session/` in the server working directory: `qr.png` and `qr_status.json` during login, `chrome.pid` for the debug Chrome, and a redacted probe before `clear_session`. The folder is git-ignored.

## Privacy and safety

- The server reads your own account. It does not use any LINE API and does not send your data anywhere except back to your MCP client.
- `probe_session` returns key names with type and length only, never secret values.
- `clear_session` is the only destructive tool. Without `confirm=true` it refuses safely and changes nothing.
- Nothing is saved unless you ask for it through your client; read tools leave no files behind.

## Troubleshooting

- **Login looks flaky.** Call `line_status` with `timeout_sec=0`: that check can never open a window, so it reports `LoginRequired` without waiting on you.
- **`AttachFailed` with "Playwright Sync API inside the asyncio loop".** This happens when `line_status` reuses a Playwright connection that is already active. The tool resets the client before each call to avoid this; if you see it, the session directory may be corrupt. Try `clear_session` to start fresh.
- **A search says `truncated`.** The scan ran out of budget before the oldest message. Raise `LINE_EXT_MSG_SEARCH_SCROLL_MS`, or ask for a larger `limit_per_room`, and repeat. Do not read an empty result as "nothing there".
- **The extension is missing.** The first call opens the Web Store page and keeps polling until the install finishes, then continues on its own.
- **Rooms cannot be read after a LINE update.** The selectors live in `line-ext-msg`. Dump the DOM and send it upstream to tune them.
- **The first call fails with a client-side timeout.** A cold Chrome start can take longer than your MCP client's default request timeout. Retry once Chrome is warm, or raise the server `timeout` in your client config. A login wait is bounded by `timeout_sec` on `line_status`, but the underlying Chrome start is not.
- **HTTP transport cannot log in.** The QR window needs an interactive desktop session on the machine running the server. Use it for reading only, and log in through a stdio client first.

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
uvx --from ./dist/line_desktop_mcp-3.0.0-py3-none-any.whl line-desktop-mcp --help
```

### Tests

```bash
uv run python -m unittest discover -s tests -v
```

Browser-free unit tests only (room resolving, envelopes and error hints, login status, session gate, shared-client lifecycle, scroll-stop reporting, env defaults, CLI defaults). No Chrome needed.

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
  client.py         # the one LineClient, its lock, and stale-page recovery
  tools/line_msg.py # LINE tools (status, rooms, messages, search)
  tools/session.py  # Session tools (probe_session read-only, clear_session destructive)
```

`client.py` owns the Chrome connection for the server's lifetime. Everything
else borrows it through `shared_client()`, which holds a lock for the duration
of a tool call and drops the client if the call fails, so a dead tab is rebuilt
instead of reused. `line_status` calls `reset()` before running the readiness
checks, because `status()` always starts a new Playwright instance and reusing
a client that already holds one leaks the old connection.

Add a new tool domain by creating `tools/<domain>.py` with a `register_<domain>_tools(mcp)` function, then calling it from `server.py`.

### Publish

```bash
uv build
uv publish
```

Requires `requires-python = ">=3.10"` and the `line-desktop-mcp` console script defined in `pyproject.toml`.
