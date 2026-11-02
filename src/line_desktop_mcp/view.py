"""Turn raw LINE message dicts into the shapes this server exposes.

Two shapes come out of here:

* a *view* for the model -- compact, ordered, with a readable `summary` for
  anything that is not text;
* a *row* for the archive -- the columns SQLite stores, plus the untouched
  payload so a later session with E2EE keys can retry decryption.

Everything is pure: no network, no database, no Tk.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from okline.enums import ContentType


def kind_name(content_type: Any) -> str:
    """Short, stable name for a LINE content type (`text`, `image`, ...)."""
    try:
        value = ContentType(int(content_type or 0))
    except (TypeError, ValueError):
        return f"type{content_type}"
    return "text" if value is ContentType.NONE else value.name.lower()


#: Every name `kind_name` can return, taken from the SDK enum so the accepted
#: list cannot drift. `typeN` is also accepted, for unknown content types.
KIND_NAMES = frozenset(
    {"text"} | {member.name.lower() for member in ContentType if member is not ContentType.NONE}
)


def known_kind(name: str) -> bool:
    """True when a `kinds` filter can use this name."""
    lowered = name.strip().lower()
    return lowered in KIND_NAMES or (lowered.startswith("type") and lowered[4:].isdigit())


def iso_time(epoch_ms: Any) -> str | None:
    """Epoch milliseconds to a local ISO 8601 string (what a model reads best).

    Conversion goes through UTC first: `fromtimestamp` on a naive datetime
    fails on Windows for instants before the epoch in some time zones.
    """
    try:
        value = int(epoch_ms)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        moment = datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone()
    except (OSError, OverflowError, ValueError):
        return None
    return moment.isoformat(timespec="seconds")


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _meta(message: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = message.get("contentMetadata")
    return meta if isinstance(meta, Mapping) else {}


def _text_of(message: Mapping[str, Any]) -> str | None:
    text = message.get("text")
    if not isinstance(text, str):
        return None
    return text.strip() or None


def describe(kind: str, message: Mapping[str, Any], text: str | None) -> str | None:
    """One human sentence for a non-text message; None when the text says it all."""
    meta = _meta(message)
    raw_location = message.get("location")
    location: Mapping[str, Any] = raw_location if isinstance(raw_location, Mapping) else {}

    if kind == "image":
        return "sent an image"
    if kind == "video":
        return "sent a video"
    if kind == "audio":
        return "sent a voice message"
    if kind == "sticker":
        package = meta.get("STKPKGID") or ""
        return f"sent a sticker (package {package})" if package else "sent a sticker"
    if kind == "file":
        name = meta.get("FILE_NAME") or "a file"
        size = _int(meta.get("FILE_SIZE"))
        return f"sent a file {name}" + (f" ({size} bytes)" if size else "")
    if kind == "location":
        title = (location or {}).get("title") or "a place"
        return f"shared a location: {title}"
    if kind == "contact":
        name = meta.get("displayName") or meta.get("DISPLAY_NAME") or "a contact"
        return f"shared a contact: {name}"
    if kind == "flex":
        alt = meta.get("ALT_TEXT") or "a card"
        return f"sent a card: {alt}"
    if kind == "link":
        return "sent a link"
    if kind == "call":
        return "call"
    if kind == "chimevent":
        return "chat event"
    if kind == "unsent":
        return "message was unsent"
    if kind == "text" and not text:
        return "empty message"
    if kind == "text":
        return None
    return f"sent a {kind} message"


def detail(kind: str, message: Mapping[str, Any]) -> dict[str, Any]:
    """Typed fields worth keeping, per kind. Empty when nothing useful exists."""
    meta = _meta(message)
    raw_location = message.get("location")
    location: Mapping[str, Any] = raw_location if isinstance(raw_location, Mapping) else {}

    if kind == "sticker":
        return _wrapped(
            "sticker",
            {
                "package_id": meta.get("STKPKGID"),
                "sticker_id": meta.get("STKID"),
                "version": meta.get("STKVER"),
                "text": meta.get("STKTXT"),
            },
        )
    if kind == "location":
        return _wrapped(
            "location",
            {
                "title": location.get("title"),
                "address": location.get("address"),
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
            },
        )
    if kind == "contact":
        return _wrapped(
            "contact",
            {
                "mid": meta.get("mid") or meta.get("MID"),
                "name": meta.get("displayName") or meta.get("DISPLAY_NAME"),
            },
        )
    if kind == "file":
        return _wrapped(
            "file", {"name": meta.get("FILE_NAME"), "size": _int(meta.get("FILE_SIZE"))}
        )
    if kind in {"image", "video", "audio", "extimage"}:
        return _wrapped(
            "media",
            {
                "size": _int(meta.get("FILE_SIZE")),
                "duration_ms": _int(meta.get("DURATION")),
            },
        )
    if kind == "flex":
        return _wrapped("flex", {"alt_text": meta.get("ALT_TEXT")})
    if kind == "chimevent":
        members = meta.get("members") or meta.get("TARGET_USER_MIDS") or []
        return _wrapped("event", {"type": meta.get("type"), "members": list(members)})
    return {}


def _wrapped(key: str, values: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the wrapper only when it actually carries a value."""
    compact = _compact(values)
    return {key: compact} if compact else {}


def _compact(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def to_view(
    raw: Mapping[str, Any],
    names: Mapping[str, str],
    decrypt: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """One message as the model sees it: compact, readable, token-frugal."""
    message = raw
    sealed = bool(raw.get("chunks"))
    if sealed and decrypt is not None:
        try:
            message = decrypt(raw)
        except Exception:  # noqa: BLE001 - a bad key must not kill the list
            message = raw

    text = _text_of(message)
    kind = kind_name(message.get("contentType"))
    from_mid = str(message.get("from") or "")
    view: dict[str, Any] = {
        "id": str(message.get("id") or raw.get("id") or ""),
        "at": iso_time(message.get("deliveredTime") or message.get("createdTime")),
        "from": names.get(from_mid) or (from_mid[:10] or None),
        "kind": kind,
        "text": text,
    }
    if sealed and not text:
        view["sealed"] = True
    view.update(detail(kind, message))
    summary = describe(kind, message, text)
    if summary:
        view["summary"] = summary
    return {key: value for key, value in view.items() if value is not None or key == "text"}


def to_row(
    raw: Mapping[str, Any],
    names: Mapping[str, str],
    chat_mid: str,
    decrypt: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """One message as the archive stores it (raw payload kept for later retries)."""
    message = raw
    sealed = bool(raw.get("chunks"))
    if sealed and decrypt is not None:
        try:
            message = decrypt(raw)
        except Exception:  # noqa: BLE001 - store it sealed and move on
            message = raw

    from_mid = str(raw.get("from") or "")
    return {
        "id": str(raw.get("id") or ""),
        "chat_mid": chat_mid,
        "from_mid": from_mid or None,
        "from_name": names.get(from_mid),
        "delivered_ms": _int(raw.get("deliveredTime") or raw.get("createdTime")) or 0,
        "kind": kind_name(message.get("contentType")),
        "text": _text_of(message),
        "sealed": 1 if (sealed and not _text_of(message)) else 0,
        "content_type": _int(message.get("contentType")) or 0,
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


def row_to_view(row: Mapping[str, Any], names: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Rebuild the model-facing shape from an archived row."""
    raw: Mapping[str, Any] = {}
    stored = row.get("raw_json")
    if isinstance(stored, str) and stored:
        try:
            parsed = json.loads(stored)
            raw = parsed if isinstance(parsed, Mapping) else {}
        except ValueError:
            raw = {}

    text = row.get("text") or None
    kind = str(row.get("kind") or "text")
    from_mid = str(row.get("from_mid") or "")
    sender = (names or {}).get(from_mid) or row.get("from_name") or (from_mid[:10] or None)
    view: dict[str, Any] = {
        "id": str(row.get("id") or ""),
        "at": iso_time(row.get("delivered_ms")),
        "from": sender,
        "kind": kind,
        "text": text,
    }
    if row.get("sealed"):
        view["sealed"] = True
    view.update(detail(kind, raw))
    summary = describe(kind, raw, text)
    if summary:
        view["summary"] = summary
    return {key: value for key, value in view.items() if value is not None or key == "text"}


def match_chat(query: str, chats: list[dict[str, Any]]) -> tuple[dict | None, list[dict]]:
    """Resolve a mid or a name to one chat.

    Exact mid beats exact name beats substring. Returns `(chat, [])` on a hit
    and `(None, candidates)` when the query is ambiguous or unknown, so the
    caller can show choices instead of silently picking the first match.
    """
    needle = (query or "").strip().casefold()
    if not needle:
        return None, []

    for chat in chats:
        if str(chat.get("mid") or "").casefold() == needle:
            return chat, []

    exact = [chat for chat in chats if str(chat.get("name") or "").casefold() == needle]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact

    partial = [chat for chat in chats if needle in str(chat.get("name") or "").casefold()]
    if len(partial) == 1:
        return partial[0], []
    return None, partial
