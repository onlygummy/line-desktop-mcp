# Version history

How version 4 differs from versions 1 to 3.

## v1 to v3

Versions 1 to 3 drove headless Chrome with the LINE Chrome Extension through the
`line-ext-msg` library: Playwright, CDP, a Tk QR dialog, and DOM scraping.

## v4

Version 4 is a full rewrite on the OkLine protocol SDK. There is no browser, no
CDP port, and no extension install. It replaces the earlier implementation
rather than building on it, and the previous code is kept outside this
repository.

"Desktop" in the name is inherited. It does not mean the LINE desktop app.

## After v4

The tool surface is `login`, `logout`, `list_chats`, `get_messages`, and
`search_messages`. See the [tool reference](tools.md) for their behaviour.
