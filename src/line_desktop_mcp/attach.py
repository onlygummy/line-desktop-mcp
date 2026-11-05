"""Save image messages once, then hand them back for the model to see.

Single responsibility: media attachment. Both `get_messages` and `unread`
return messages that may contain images, and both must save each file exactly
once, reuse the backup on the next call, and never fail a listing over one bad
download.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from line_desktop_mcp import media, paths
from line_desktop_mcp.archive import Archive
from line_desktop_mcp.reader import LineReader


def attach_images(
    reader: LineReader,
    archive: Archive,
    chat_mid: str,
    items: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    max_images: int | None = None,
    max_image_bytes: int | None = None,
) -> tuple[list[tuple[str, Path]], list[str], list[str]]:
    """Save each image once, then return `(message_id, path)` pairs.

    `items` holds the image messages whose bytes are wanted, each with the raw
    payload `download()` needs. Ids whose bytes cannot be fetched or are not an
    image come back in the failure list. Ids omitted because of `max_images` or
    `max_image_bytes` come back in the third list; a byte-capped image is still
    archived so a later call can use it without downloading it again.
    """
    wanted = [(message_id, raw) for message_id, raw in items if message_id]
    if not wanted:
        return [], [], []

    known = archive.media_for([message_id for message_id, _raw in wanted])
    attached: list[tuple[str, Path]] = []
    failed: list[str] = []
    skipped: list[str] = []

    for message_id, raw in wanted:
        if max_images is not None and len(attached) >= max_images:
            skipped.append(message_id)
            continue

        saved = known.get(message_id)
        if saved:
            if max_image_bytes is not None and int(saved.get("bytes") or 0) > max_image_bytes:
                skipped.append(message_id)
                continue
            path = paths.media_dir() / str(saved.get("rel_path"))
            if path.exists():
                attached.append((message_id, path))
                continue

        data = reader.download(raw or {"id": message_id})
        if not data:
            failed.append(message_id)
            continue
        ext = media.image_ext(data)
        if ext is None:
            failed.append(message_id)
            continue

        path = archive.media_path(chat_mid, message_id, ext)
        media.save_bytes(path, data)
        archive.record_media(message_id, chat_mid, path, data, ext)
        if max_image_bytes is not None and len(data) > max_image_bytes:
            skipped.append(message_id)
            continue
        attached.append((message_id, path))

    return attached, failed, skipped


def raw_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """The stored raw message of one archive row, for a re-download attempt."""
    stored = row.get("raw_json")
    if not isinstance(stored, str) or not stored:
        return {}
    try:
        parsed = json.loads(stored)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
