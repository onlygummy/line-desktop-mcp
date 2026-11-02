# get_messages

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
