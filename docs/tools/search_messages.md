# search_messages

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
