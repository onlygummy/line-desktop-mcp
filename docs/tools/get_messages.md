# get_messages

```text
get_messages(
    chat: str,
    limit: int = 30,
    after: str | None = None,
    before: str | None = None,
    around: str | None = None,
    context: int = 5,
    include_images: bool = True,
    max_images: int | None = 20,
    max_image_bytes: int | None = None,
)
```

- `chat` accepts a chat mid or part of a chat name. An exact mid wins, then an
  exact name, then a substring. If more than one chat matches, the call fails
  with the candidate list instead of guessing.
- `limit` is 1 to 200 and caps how many messages come back.
- `after` and `before` read a time window instead of the newest slice. They take
  a date (`YYYY-MM-DD`) or an ISO timestamp; a bare date means local midnight,
  and `before` includes the whole day. An empty window is refused.
- A window is answered from the archive first, so a window read before costs
  nothing and works offline. LINE cannot jump to a date, so filling the rest
  means walking back from the newest message, at most five pages; `partial: true`
  says the budget ran out before the window's older edge.
- `around` takes a message id from an earlier result and returns the messages
  around it, with `context` messages on each side (1 to 50). It comes from the
  archive only, so the id must have been read before; `limit` is ignored in this
  mode, and `after` / `before` cannot be combined with it.
- Messages are ordered oldest first and shaped `{id, at (local ISO 8601), from,
  kind, text, ...}`.
- `kind` is `text`, `image`, `video`, `audio`, `sticker`, `file`, `location`,
  `contact`, `flex`, `chimevent`, `link`, `call`, or `typeN` for unknown content
  types.
- Non-text messages add a readable `summary` and a typed detail object
  (`sticker`, `location`, `contact`, `file`, `media`, `flex`, `event`), such as
  "sent a sticker (package 11537)" or "sent a file report.pdf (1234 bytes)".
- `include_images` defaults to true. Set it to false to keep the image message
  rows without attaching image blocks.
- `max_images` defaults to 20 and caps how many image blocks one call attaches.
  Set it to `null` to remove the cap for one call. `max_image_bytes` skips an
  image whose bytes are larger than the cap. Omitted ids are listed in
  `images_skipped`. The byte cap is off by default.

The tool returns MCP content blocks: one text block with JSON, then one image block
per attached image. The JSON is `{ok, chat{mid,name,kind}, messages[], returned,
window{after,before}, around, source, e2ee_ready, stale, partial,
images[{message_id,path}], images_failed[], images_skipped[]}`.
