# Tools

Reference for the five MCP tools: `login`, `logout`, `list_chats`, `get_messages`,
and `search_messages`.

## Shared behaviour

- Every tool declares `read_only_hint: false` and `idempotent_hint: true`, because
  all of them touch local files. `logout` is the only one with
  `destructive_hint: true`, because it deletes the saved login.
- Newest messages always come from LINE on each call. Older messages are read from
  the archive first, and the tool pages backwards through LINE only after that.
- Images are always attached when present, with no count or size cap. Each image
  is saved under the media directory before it is attached.
- `images_failed` lists the ids whose bytes could not be fetched or were not an
  image. Sealed images cannot be decrypted by the pinned OkLine 2.7, so they
  archive as sealed rows without an attachment.
- `source` is `api`, `mixed`, or `db`.
- `stale: true` means LINE was unreachable and the result came from the archive.
- `partial: true` only when the five-page budget ran out. A short chat is not
  partial.
- `sealed: true` means the message is Letter-Sealed and the session could not
  open it, so the message carries no text.

## login

```text
login(timeout_sec: float = 300, force: bool = False)
```

Call this tool first.

- When the saved session works, the call returns at once with
  `already_logged_in: true` and `ui: "none"`, and no window opens.
- Otherwise it opens a Tk window titled `Line Desktop MCP` in a child process,
  shows the QR code and any PIN LINE asks for, and waits.
- On success it saves the session file and returns `already_logged_in: false`
  with `ui: "window"`. A finished window closes itself after about a second.
- Closing the window stops the wait and saves nothing.
- `timeout_sec` is 10 to 900. `force=true` replaces a working session and always
  shows a fresh QR.
- One login runs at a time. A second concurrent call is refused.
- If the window cannot open (no local display, or a Python build without Tk),
  the QR is written to `qr.txt` beside the session file and the result reports
  `ui: "text"`. That file is deleted when the call ends, because the code is
  single-use.
- The window appears on the machine that runs the server, so a login cannot be
  completed from a different host over a non-local transport.
- Failures arrive as MCP tool errors (`ToolError`) instead of result data.

The result is `{ok, already_logged_in, profile{mid, display_name}, mid,
e2ee_ready, session_path, ui}`.

## logout

```text
logout()
```

- Asks LINE to invalidate the session, then deletes `session.json` and any
  `qr.txt`. `remote` reports `revoked`, `skipped`, or `failed`; the local file
  goes either way, because the stored token is the real risk. The phone stays
  logged in; only this server signs out.
- The archive and the image backups are kept, but every tool needs a `login`
  again before it can reach LINE.
- Safe to repeat (`already_logged_out: true`); refused while a `login` runs.

The result is `{ok, already_logged_out, session_deleted, remote, remote_error,
archive, next}`.

## list_chats

```text
list_chats(unread_only: bool = False, query: str | None = None, limit: int = 30)
```

- `unread_only` keeps only chats with unread messages.
- `query` is a case-insensitive substring filter on the chat name.
- `limit` is 1 to 100.
- The listing is cached for five minutes. Every tool that reads the directory
  saves it to the archive, so names and unread counts survive a cold start; when
  LINE is unreachable, the call still answers from the archive with `stale: true`.
- Nothing is marked as read.

The result is `{ok, chats[], returned, unread_total, stale}`. Each chat is
`{mid, name, kind, unread, last_ms, last_preview}`. `kind` is `group`, `room`, or
`dm`. A chat is named after its LINE name, else a member, else its mid.

## get_messages

```text
get_messages(chat: str, limit: int = 30)
```

- `chat` accepts a chat mid or part of a chat name. An exact mid wins, then an
  exact name, then a substring. If more than one chat matches, the call fails
  with the candidate list instead of guessing.
- `limit` is 1 to 200.
- Messages are ordered oldest first and shaped `{id, at (local ISO 8601), from,
  kind, text, ...}`.
- `kind` is `text`, `image`, `video`, `audio`, `sticker`, `file`, `location`,
  `contact`, `flex`, `chimevent`, `link`, `call`, or `typeN` for unknown content
  types.
- Non-text messages add a readable `summary` and a typed detail object
  (`sticker`, `location`, `contact`, `file`, `media`, `flex`, `event`), such as
  "sent a sticker (package 11537)" or "sent a file report.pdf (1234 bytes)".

The tool returns MCP content blocks: one text block with JSON, then one image block
per image. The JSON is `{ok, chat{mid,name,kind}, messages[], returned, source,
e2ee_ready, stale, partial, images[{message_id,path}], images_failed[]}`.

## search_messages

```text
search_messages(
    query: str,
    chats: list[str] | None = None,
    limit: int = 20,
    since_days: int | None = None,
    sync: bool = False,
)
```

- `query` is a case-insensitive substring. Thai works because the comparison is
  a plain substring, not a word tokenizer.
- `chats` narrows the search to chat mids or names. Omit it to search every
  archived chat. An ambiguous or unknown name fails with the candidate list, the
  same way `get_messages` does.
- `limit` is 1 to 100 hits, newest first.
- `since_days` keeps only messages from the last N days, from 1 to 3650.
- `sync=true` reads the newest page of the named chats and then pages older
  messages from LINE before searching, at most three older pages per chat. The
  newest page is read first so a message that arrived after the chat was
  archived can still match. `sync` needs `chats`; without them the call is
  refused, because paging every chat would take minutes.
- Only plain text is searched. Images, stickers, and sealed messages that could
  not be decrypted never match. There is no fuzzy matching and no spelling
  tolerance.

Each call returns `{ok, query, scope, hits[{chat{mid,name}, message{...}}], returned,
chats_hit[{mid,name,hits}], coverage{messages_searched, chats_total},
api_pages}`.

- `scope` is `archive` or `archive+api`.
- `coverage.messages_searched` counts only what the archive holds, so a small
  number means most history was never read.
- `chats_hit` tells you which chat mentioned the text, so you do not have to
  group the hits.
- `api_pages` reports how many LINE pages `sync` fetched, newest page included.
