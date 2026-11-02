# Security Policy

## What this server holds

`line-desktop-mcp` 4.0.0 logs in to your own LINE account by QR and keeps a
local, plaintext copy of what it reads. Everything lives in one directory:
`LINE_DESKTOP_MCP_HOME` when set, otherwise `%LOCALAPPDATA%\line-desktop-mcp` on
Windows or `~/.local/share/line-desktop-mcp` elsewhere. The paths and what each
one holds are listed in [local storage](docs/storage.md).

The session file is a password. The archive and the media directory are
plaintext copies of your chats and images. Treat the whole directory as
sensitive. `.gitignore` excludes `session/`, `*.session.json`, `tokens.json`,
`qr.txt`, `archive.db`, `archive.db-wal`, `archive.db-shm`, and `media/`, but a
backup or cloud sync of the folder would still copy it somewhere else. Do not
commit it, and do not copy or sync it to another machine or a cloud drive.

To remove everything, delete the whole directory. A new login creates a new
session; archived messages from before are gone.

## If the session file leaks

Anyone with the file can act as the logged-in device, so revoke it:

1. Open the LINE app and go to Settings → Account → logged-in devices.
2. Remove the device.
3. Delete the session file.
4. Call `login` again to log in from scratch.

## The QR window

`login` opens a Tk window on the machine running the server. It shows a QR
code, and sometimes a PIN. This window authorises a new device on the account.
Anyone who can see or photograph it can complete the login.

Log in somewhere private. Closing the window stops the login and saves nothing.

## Threat model for an MCP client

stdio has no authentication. Whatever client can spawn the server can call
every tool it exposes. The server has six tools: `login`, `logout`,
`list_chats`, `get_messages`, `search_messages`, and `unread`. The client can
read chats, their messages, their images, and any text already archived across
every chat. It cannot send messages, mark a chat as read, or delete anything in
LINE.

Every tool changes files on this machine. `login` writes the session and
`logout` deletes it; the three read tools write the archive, and `get_messages`
also writes image backups. A client that can call them can also fill the local
disk over time.

Tool results become model context at the provider the user chose. The MCP
client and that provider are part of the trust boundary: grant the server only
to a client and a model provider you trust.

The server offers no network transport. Its only channel is stdio.

## Reporting a vulnerability

Do not open a public issue. Use this repository's GitHub private security
advisory and expect a reply within a few days.

Problems in OkLine itself, including the SDK, its WASM bridge, or its signing,
go to the upstream project at https://github.com/NiceDayZc/OkLine.

Problems in LINE's own services go to LINE Corporation, not here.

## Scope and responsible use

This project is unofficial and independent. Use it only with your own account.
Automating a personal LINE account violates LINE's Terms of Service, and your
account can be restricted or banned.

Do not use this server for spam, for scraping other people's data, for account
takeover, or for any other abuse.
