# search_messages

```text
search_messages(
    query: str | None = None,
    kinds: list[str] | None = None,
    chats: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
    after: str | None = None,
    before: str | None = None,
    from_sender: str | None = None,
    order: "newest" | "oldest" = "newest",
    since_days: int | None = None,
    sync: bool = False,
)
```

- `query` is a case-insensitive substring. Thai works because the comparison is
  a plain substring, not a word tokenizer. Omit it to search by `kinds` alone;
  at least one of the two is required.
- `kinds` keeps only those message kinds, such as `text`, `image`, `file`,
  `sticker`, `video`, `audio`, `location`, `contact`, `flex`, `chimevent`,
  `link`, or `call`. An unknown name fails with the accepted list.
  `kinds=["image"]` with no `query` is how you find photos and files, which
  never have text.
- `chats` narrows the search to chat mids or names. Omit it to search every
  archived chat. An ambiguous or unknown name fails with the candidate list, the
  same way `get_messages` does.
- `limit` is 1 to 100 hits. `offset` skips hits for paging, and
  `truncated: true` means more hits exist after this page.
- `after` and `before` take a date (`YYYY-MM-DD`) or an ISO timestamp. A bare
  date means local midnight, and `before` includes the whole day. When both
  `after` and `since_days` are given, `after` wins. An empty window is refused.
- `from_sender` takes a sender mid, or an exact then partial name, matched only
  against senders already archived. More than one match fails with the
  candidates.
- `order` is `newest` (default) or `oldest`, for finding where a topic started.
- `since_days` keeps only messages from the last N days, from 1 to 3650.
- `sync=true` reads the newest page of the named chats and then pages older
  messages from LINE before searching, at most three older pages per chat. The
  newest page is read first so a message that arrived after the chat was
  archived can still match. `sync` needs `chats`; without them the call is
  refused, because paging every chat would take minutes.

A `query` only matches messages that have text: images, stickers, and sealed
messages that could not be decrypted never match. Use `kinds` to find those.

Each call returns `{ok, query, scope, filters, hits[{chat{mid,name},
message{...}, snippet}], returned, truncated, chats_hit[{mid,name,hits}],
coverage{messages_scanned, messages_in_scope, messages_sealed, chats_total},
api_pages}`.

- `filters` echoes what was applied:
  `{chats, kinds, after, before, from{mid,name}, order, offset}`.
- `snippet` is about 60 characters around the first match, with `…` where it
  was cut. It is `null` when the search had no `query`. `message.text` still
  carries the whole message.
- `scope` is `archive` or `archive+api`.
- `coverage.messages_scanned` counts the messages the text match could see. With
  no `query`, every in-scope message is a candidate, so it equals
  `messages_in_scope`. `messages_in_scope` counts every message the filters
  select, so the gap is what a text search skips: images, stickers, and the
  `messages_sealed` rows it cannot read. A small `messages_scanned` means most
  history was never read.
- `chats_hit` tells you which chat mentioned the text, so you do not have to
  group the hits.
- `api_pages` reports how many LINE pages `sync` fetched, newest page included.
