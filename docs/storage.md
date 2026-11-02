# Local storage

What the server writes on this machine, where the files live, and what that
means for privacy.

## Files

Everything lives in one directory: `%LOCALAPPDATA%\line-desktop-mcp` on Windows,
or `~/.local/share/line-desktop-mcp` elsewhere. `LINE_DESKTOP_MCP_HOME` moves the
whole directory; see [configuration](configuration.md).

| Path | Contents |
|---|---|
| `session.json` | access token, refresh token, E2EE keychain |
| `archive.db` | SQLite with WAL, tables `chats`, `messages`, `media` |
| `media\<chat_mid>\<message_id>.<ext>` | downloaded image backups |

Reading chats also saves the chat list, so names and unread counts are still
there on a cold start with LINE unreachable.

## Plaintext and removal

The files in the directory are plaintext, so treat the whole directory as
sensitive. Opening an image downloads it from LINE object storage into `media`.

`logout` deletes `session.json` and any fallback `qr.txt`, and leaves the
archive and the media directory in place.

`.gitignore` excludes `session/`, `*.session.json`, `tokens.json`, `qr.txt`,
`archive.db`, `archive.db-wal`, `archive.db-shm`, and `media/`. A backup or cloud
sync of the folder can still copy it elsewhere, so keep it off shared drives.

Delete the whole directory to remove everything. A new login creates a new
session, and the archived messages from before are gone.

## What the server does not do

- It never sends to LINE, never marks a chat as read, and never deletes anything
  in LINE.
- Tool results become model context at the provider you chose. The MCP client
  and that provider are part of the trust boundary.

## Threat model

Read the [security policy](../SECURITY.md) for the threat model, the QR window
risk, and the reporting process.
