# list_chats

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
