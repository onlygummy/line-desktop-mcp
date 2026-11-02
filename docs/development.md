# Development

Commands, tests, source layout, and architecture notes.

## Commands

```bash
uv sync
uv run line-desktop-mcp
uv run pytest                  # 163 tests, no network, Node or Tk needed
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv build
```

`uv` installs `uvx`, which the client configuration uses.

## Structure

```text
src/line_desktop_mcp/
  __init__.py      # __version__
  __main__.py      # python -m line_desktop_mcp
  main.py          # FastMCP wiring: instructions, shared reader + archive, registration
  paths.py         # where the session, database and media live
  reader.py        # LineReader: one LINE client, ensure_ready(), caches, error mapping
  lookup.py        # chat lookup by mid or name, shared by the read tools
  history.py       # newest page, then older pages: keep one chat archived
  archive.py       # SQLite archive: chats, messages, media, search
  media.py         # magic-byte detection, safe file names, atomic writes
  attach.py        # save image messages once, shared by get_messages and unread
  view.py          # message -> model view / archive row, chat matching
  qr_window.py     # Tk card (child process) with the QR, PIN and outcome states
  tools/
    login.py
    logout.py
    list_chats.py
    get_messages.py
    search_messages.py
    unread.py
tests/
  test_login.py  test_logout.py  test_list_chats.py  test_get_messages.py
  test_search_messages.py  test_unread.py  test_reader.py  test_archive.py
  test_history.py  test_media.py  test_view.py  test_qr_window.py
  test_main.py  stubs.py
```

## Architecture

- The tool thread never touches Tk. The window is a child process driven by JSON
  lines on stdin, because Tk must own its process main thread.
- The blocking `qr_login` call runs in a worker thread.
- Every read tool calls `ensure_ready()` first.
- The LINE client is one shared instance, guarded by a lock, because
  `requests.Session` and the Node signer are not thread safe.
- The archive is one SQLite connection with WAL.
- `logout` revokes the session on LINE, closes the client while holding the call
  lock, then deletes the session file. The archive is never in scope.

## Protocol

The server runs on the unofficial OkLine Python SDK, which speaks the LINE
Chrome-extension protocol: the gateway `line-chrome-gw.line-apps.com`, `X-Hmac`
signatures computed by LINE's `ltsm.wasm` through a Node.js bridge, and E2EE
Letter Sealing. FastMCP provides the MCP layer.

The Node bridge needs Node.js 18 or newer on `PATH`. Set `LINE_NODE` to use a
different binary. Tk support must be in the Python build: the python.org
installers include it, and uv-managed CPython 3.14 on Windows reports Tk 8.6.

## Tests

The suite needs no network, Node, or Tk. `tests/stubs.py` holds the shared
fakes.
