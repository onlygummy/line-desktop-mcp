# line-desktop-mcp

[![ci](https://github.com/onlygummy/line-desktop-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/onlygummy/line-desktop-mcp/actions/workflows/ci.yml)

An MCP server that reads your own LINE chats. It logs in by scanning a QR code in a Tk window,
lists chats, returns messages with their images, and can sign out again. It uses the unofficial
OkLine SDK and FastMCP.
Version 4.0.0. It is not affiliated with LINE Corporation or LY Corporation.

> **Read this first: Terms of Service.** Automating a personal LINE account
> violates LINE's Terms of Service. Your account can be restricted or banned.
> Use only your own account, keep volumes human-like, and treat the session
> file as a password.
>
> This server also keeps a plaintext local copy of the chats and images it
> reads.

## What it does

- `login` scans a QR code and saves the LINE session.
- `logout` ends the session and deletes the saved login; the archive is kept.
- `list_chats` lists chats with name, kind, unread count, and a last preview.
- `get_messages` reads one chat and returns its messages, with images attached.
- `search_messages` searches the local archive for text across chats.
- `unread` summarises every chat with unread messages in one call.

It never writes to LINE: it does not send, mark anything as read, or delete anything. Every tool
writes on this machine: the session, the message archive, and the image backups.

## Quick start

You need Python 3.10+ with Tk and `uv`, Node.js 18+ on `PATH`, a LINE account with its phone signed
in, and Windows, macOS, or Linux. The QR window needs a local desktop session.

### Install from PyPI

Add the server to Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "line-desktop-mcp": {"command": "uvx", "args": ["line-desktop-mcp@latest"]}
  }
}
```

Or to `opencode.json`:

```json
{
  "mcp": {
    "servers": {
      "line-desktop-mcp": {"type": "local", "command": ["uvx", "line-desktop-mcp@latest"]}
    }
  }
}
```

### Run from a checkout

```bash
git clone https://github.com/onlygummy/line-desktop-mcp.git
cd line-desktop-mcp
uv sync
```

Point the client at the checkout:

```json
{
  "mcpServers": {
    "line-desktop-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/line-desktop-mcp", "line-desktop-mcp"]
    }
  }
}
```

1. Ask the client to call `login`.
2. A window titled `Line Desktop MCP` opens on the machine running the server.
3. Scan the code with the LINE app, and enter the PIN if LINE asks for one.
4. Call `list_chats` to find the chat you want.
5. Call `get_messages` with the chat mid or part of its name.

## Documentation

Read the [tool reference](docs/tools.md), [configuration](docs/configuration.md),
[local storage](docs/storage.md), [troubleshooting](docs/troubleshooting.md),
[development](docs/development.md), and [version history](docs/history.md).
See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## Privacy and safety

- Treat the session file as a password: it holds your tokens and Letter Sealing keys.
- Treat the archive and the media directory as sensitive: both are plaintext copies.
- Tool results become model context at the provider you chose, which is part of the trust boundary.

Read the [security policy](SECURITY.md) for the threat model and reporting process.

## License

MIT. OkLine is MIT and unofficial. Not affiliated with LINE or LY Corporation.
