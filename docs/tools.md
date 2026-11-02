# Tools

Reference for the six MCP tools: `login`, `logout`, `list_chats`,
`get_messages`, `search_messages`, and `unread`. Each one has its own page.

## Shared behaviour

- Every tool declares `read_only_hint: false` and `idempotent_hint: true`, because
  all of them touch local files. `logout` is the only one with
  `destructive_hint: true`, because it deletes the saved login.
- `get_messages` always reads the newest messages from LINE, fills older ones from
  the archive first, and only then pages backwards through LINE. `unread` reads the
  newest messages directly and never pages back.
- `get_messages` attaches every image it finds, with no count or size cap, and
  saves it under the media directory before attaching it. `unread` does the same
  only when `include_images=true`.
- `images_failed` lists the ids whose bytes could not be fetched or were not an
  image. Sealed images cannot be decrypted by the pinned OkLine 2.7, so they
  archive as sealed rows without an attachment.
- `source` is `api`, `mixed`, or `db`.
- `stale: true` means LINE was unreachable and the result came from the archive.
- `partial: true` only when the five-page budget ran out. A short chat is not
  partial.
- `sealed: true` means the message is Letter-Sealed and the session could not
  open it, so the message carries no text.

## Tools

| Tool | Answers | Page |
|---|---|---|
| `login` | who am I, and is the session still valid? | [login](tools/login.md) |
| `logout` | sign this device out and delete the saved login | [logout](tools/logout.md) |
| `list_chats` | which chats exist, and what is unread? | [list_chats](tools/list_chats.md) |
| `get_messages` | what was said in this chat? | [get_messages](tools/get_messages.md) |
| `search_messages` | where was this text said? | [search_messages](tools/search_messages.md) |
| `unread` | what did I miss across all chats? | [unread](tools/unread.md) |
