# line-desktop-mcp

An MCP server that logs in to your own personal LINE account by scanning a QR
code in a Tk window. It is built on the unofficial OkLine Python SDK, which
speaks the LINE Chrome-extension protocol: the gateway
`line-chrome-gw.line-apps.com`, `X-Hmac` signatures computed by LINE's real
`ltsm.wasm` through a Node.js bridge, and E2EE Letter Sealing. FastMCP provides
the MCP layer.

Version 4.0.0. Unofficial, not affiliated with LINE Corporation or LY
Corporation.

> **Read this first: Terms of Service.** Automating a personal LINE account
> violates LINE's Terms of Service. Your account can be restricted or banned.
> Use only your own account, keep volumes human-like, and treat the session
> file as a password.

## What it does

The server exposes exactly one tool, `line_login`. Reading tools for chats,
messages, and search are planned. Until they exist the server is read-only by
design: it never sends a message, never marks a chat as read, and never deletes
anything.

## Version history

Version 4 is a full rewrite.

- v1 to v3 drove headless Chrome with the LINE Chrome Extension through the
  `line-ext-msg` library: Playwright, CDP, a Tk QR dialog, and DOM scraping.
- v4 removes Chrome and the extension. It uses OkLine, the protocol SDK, so
  there is no browser, no CDP port, and no extension install.
- The previous implementation is kept outside this repository. Version 4
  replaces it rather than building on it.
- "Desktop" in the name is inherited. v4 does not touch the LINE desktop app or
  any browser.

## Requirements

- Python 3.10 or newer.
- Node.js 18 or newer on `PATH`. OkLine runs LINE's `ltsm.wasm` through a Node
  bridge to compute `X-Hmac`. Set `LINE_NODE` to use a different binary.
- Tk support in the Python build. The python.org installers include it;
  uv-managed CPython 3.14 on Windows reports Tk 8.6.
- `uv`, which provides `uvx`.
- A LINE account, plus the phone that is already signed in to it.

## Quick start

Add the server to Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "line-desktop-mcp": {
      "command": "uvx",
      "args": ["line-desktop-mcp@latest"]
    }
  }
}
```

Or to `opencode.json`:

```json
{
  "mcp": {
    "servers": {
      "line-desktop-mcp": {
        "type": "local",
        "command": ["uvx", "line-desktop-mcp@latest"]
      }
    }
  }
}
```

Then:

1. Ask the client to call `line_login`.
2. A window titled `Line Desktop MCP` opens on the machine running the server.
3. Scan the code with the LINE app, and enter the PIN if LINE asks for one.
4. The session is saved and the window closes.

The window only appears on the machine that runs the server. Log in from a
stdio client on that same machine first; a login cannot be completed over a
non-local transport.

## The `line_login` tool

```text
line_login(timeout_sec: float = 300, force: bool = False)
```

- If `session.json` exists and still works, the call returns at once. No window
  opens.
- Otherwise it opens a Tk window titled `Line Desktop MCP`, draws the QR code,
  shows the PIN LINE asks for, and waits.
- On success it saves the session file (access token, refresh token, E2EE
  keychain), shows a final message, closes the window, and returns
  `already_logged_in: false` with `ui: "window"`.
- `timeout_sec` is clamped to 10 to 900 by the schema. `force=true` ignores a
  working session and always shows a fresh QR.
- The window is a light card with a coloured status pill. It opens straight
  away with a grey `Starting` pill and a spinner while Node and LINE produce
  the code, then shows the QR under a green `Waiting` pill, or the PIN in large
  spaced digits under an amber `Enter code` pill. It ends on `Done` (green),
  `Could not continue` (red), or `Cancelled` (grey).
- There is no Cancel button: closing the window with its title-bar control stops
  the wait and saves nothing. The footer says so, and a finished window closes
  itself after about a second.
- Only one login can run at a time. A second concurrent call is refused.
- If the window cannot open at all (no display, or a Python build without Tk),
  the QR is written to `qr.txt` next to the session file and the result reports
  `ui: "text"`.
- Failures are MCP tool errors (`ToolError`), not data: a cancelled wait, a
  timeout, a LINE rejection, or missing Node. A missing or expired session is
  not an error; it falls through to the QR flow.

A call that finds a working session returns:

```json
{
  "ok": true,
  "already_logged_in": true,
  "profile": { "mid": "...", "display_name": "..." },
  "mid": "...",
  "e2ee_ready": true,
  "session_path": "C:\\Users\\YOU\\AppData\\Local\\line-desktop-mcp\\session.json",
  "ui": "none"
}
```

Replace `YOU` with your Windows user name. A fresh login returns the same keys
with `already_logged_in: false` and `ui: "window"`.

## Configuration

| Variable                   | Default                                                                                                             | Purpose                                         |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `LINE_DESKTOP_MCP_SESSION` | `%LOCALAPPDATA%\line-desktop-mcp\session.json` on Windows, `~/.local/share/line-desktop-mcp/session.json` elsewhere | Where the login is stored                       |
| `LINE_NODE`                | unset (uses `node` on `PATH`)                                                                                       | Node.js executable used by OkLine's WASM bridge |

## Privacy and safety

- The session file holds the access token, refresh token, and Letter Sealing
  keys. Treat it like a password and never commit it. `.gitignore` already
  excludes `session/`, `*.session.json`, `tokens.json`, and `qr.txt`.
- There is no telemetry, no analytics, and no third-party service. The server
  talks to LINE's gateway and nothing else.
- Reading happens only after login. The server never sends messages and never
  marks chats as read.
- Keep a single-machine setup with no backup routine: do not copy the session
  file around.

## Troubleshooting

| Symptom                                           | Fix                                                                                                                                                          |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `Node.js not found`                               | Install Node 18 or newer, or set `LINE_NODE` to the executable path.                                                                                         |
| Login times out                                   | Call `line_login` again and scan promptly, or raise `timeout_sec` (maximum 900).                                                                             |
| LINE rejects the login (code `10201` and similar) | Delete the session file and log in from scratch.                                                                                                             |
| No window appears                                 | The Python build lacks Tk. The QR is still written to `qr.txt` beside the session file.                                                                      |
| `REQUEST_INVALID_HMAC` (code `10005`)             | The bundled WASM or bridge could not sign. Make sure Node runs the package's own `okline/ltsm` files, and that the installed `okline` was not hand-modified. |

## For developers

```bash
uv sync
uv run line-desktop-mcp
uv run pytest          # 22 tests; no network, Node, or Tk needed
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```

```text
src/line_desktop_mcp/
  __init__.py       # __version__ = "4.0.0"
  __main__.py       # python -m line_desktop_mcp
  main.py           # FastMCP wiring: instructions, tool registration, main()
  qr_window.py      # Tk window in its own thread: qr_matrix/dark_cells, queue, close-to-cancel
  tools/
    __init__.py
    login.py        # register_login_tool(mcp): session check, QR flow, error mapping
tests/
  test_login.py     # faked OkLine and QRWindow: success, existing, stale, force, cancel, timeout, Node missing, lock, stdout clean, text fallback
  test_main.py      # exactly one tool, schema and annotations
  test_qr_window.py # dark_cells()
```

Architecture notes:

- The tool thread never touches widgets. The window owns its thread and receives
  messages through a queue polled by `root.after`.
- The blocking `qr_login` call runs in a worker thread, so closing the window
  returns immediately.
- A cancelled or timed-out worker is abandoned and never saves a session.
- Nothing writes to stdout, because stdout is the stdio JSON-RPC channel. The QR
  is drawn on a canvas; `okline.qrterm.print_qr` is deliberately not used.

## License

MIT. OkLine is MIT and unofficial. Not affiliated with LINE or LY Corporation.
