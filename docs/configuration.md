# Configuration

Environment variables the server reads at start. Set them in the environment
that launches the server.

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `LINE_DESKTOP_MCP_HOME` | `%LOCALAPPDATA%\line-desktop-mcp` on Windows, `~/.local/share/line-desktop-mcp` elsewhere | Moves the session, archive, and media together |
| `LINE_DESKTOP_MCP_SESSION` | `<home>\session.json` | Session file |
| `LINE_DESKTOP_MCP_DB` | `<home>\archive.db` | SQLite archive |
| `LINE_DESKTOP_MCP_MEDIA` | `<home>\media` | Image backups |
| `LINE_NODE` | unset, uses `node` on `PATH` | Node.js executable used by OkLine's WASM bridge |

## Notes

- `LINE_DESKTOP_MCP_HOME` sets the base directory. The other three variables
  override one path each, and their defaults follow the home directory.
- `LINE_NODE` names the Node binary that OkLine uses to run LINE's `ltsm.wasm`
  and compute the `X-Hmac` signature. Leave it unset to use `node` on `PATH`.
- These paths decide what [local storage](storage.md) holds. Read that page
  before you move the directory.
