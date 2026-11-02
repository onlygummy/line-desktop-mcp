# Security Policy

## What this server holds

`line-desktop-mcp` 4.0.0 logs in to your own LINE account by QR and stores that
login in one file. The file contains the access token, the refresh token, and
the E2EE / Letter Sealing keychain.

The location is:

1. `LINE_DESKTOP_MCP_SESSION`, if set;
2. otherwise `%LOCALAPPDATA%\line-desktop-mcp\session.json` on Windows;
3. otherwise `~/.local/share/line-desktop-mcp/session.json`.

Treat the file as a password. Do not commit it. `.gitignore` already excludes
`session/`, `*.session.json`, `tokens.json`, and `qr.txt`. Do not copy or sync
it to another machine or to a cloud drive.

## If the session file leaks

Anyone with the file can act as the logged-in device, so revoke it:

1. Open the LINE app and go to Settings → Account → logged-in devices.
2. Remove the device.
3. Delete the session file.
4. Call `line_login` again to log in from scratch.

## The QR window

`line_login` opens a Tk window on the machine running the server. It shows a QR
code, and sometimes a PIN. This window authorises a new device on the account.
Anyone who can see or photograph it can complete the login.

Log in somewhere private. The Cancel button, or closing the window, stops the
login and saves nothing.

## Threat model for an MCP client

stdio has no authentication. Whatever client can spawn the server can call
every tool it exposes. Today that tool is `line_login` only: the client cannot
send messages, read chats, mark a chat as read, or delete anything.

Read-only tools are planned. When they arrive, the same client will be able to
read chats and their content. Grant the server to a client you trust.

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
