# unread

```text
unread(chats_limit: int = 10, per_chat: int = 10, include_images: bool = False)
```

- Returns only chats with unread messages, ordered by latest activity.
- `chats_limit` is 1 to 20. Each chat costs one LINE request, so the cap is small
  on purpose.
- `per_chat` is 1 to 50 of the newest messages, returned oldest first.
- `include_images` is off by default, because a digest of ten chats can hold
  dozens of images. With it on, images are saved and attached the same way as in
  `get_messages`.
- Every message read is archived, so a later `search_messages` can find it.
- Nothing is marked as read on LINE. The unread counts stay until the chat is
  opened on the phone.
- One failing chat does not fail the call: it is served from the archive with
  `stale: true`, and `source` reports `mixed`.

The result is `{ok, chats[{chat{mid,name,kind}, unread, messages[], returned,
stale, images[], images_failed[]}], chats_returned, chats_skipped, unread_total,
messages_returned, source, stale, e2ee_ready}`.

- `chats_skipped` counts unread chats that did not fit in `chats_limit`.
- `unread_total` sums every unread chat, including the skipped ones.
- The top-level `stale` means the chat list itself came from the archive.
