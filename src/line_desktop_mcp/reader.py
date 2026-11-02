"""The only module that talks to LINE (single responsibility: access).

Tools never touch `okline` directly: they call this reader, which owns the
single client, the lock (a `requests.Session` and its Node signing bridge are
not thread-safe), the caches, and the mapping from library errors to an MCP
`ToolError` the model can act on.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from fastmcp.exceptions import ToolError
from okline import OkLine
from okline.exceptions import LineAuthError, LineError, LineTransportError
from okline.hmac_signer import HmacSignerError, LtsmBridge

from line_desktop_mcp import paths
from line_desktop_mcp.view import kind_name, to_view

NODE_HINT = (
    "Install Node.js 18 or newer (https://nodejs.org), or point LINE_NODE at "
    "an existing node executable, then retry."
)
LOGIN_HINT = "Call login first, then retry."
SESSION_BROKEN_HINT = "Delete that file and call login again."

#: How long a chat or contact listing is reused before asking LINE again.
CACHE_TTL_SEC = 300.0

#: Mids per `getContacts` request; the gateway rejects longer batches.
CONTACT_BATCH = 100


@dataclass(frozen=True)
class Ready:
    """What a tool needs to know before it can serve anything."""

    session_path: Path
    e2ee_ready: bool
    node_available: bool


class LineReader:
    def __init__(
        self,
        *,
        session_path: Callable[[], Path] = paths.session_path,
        api_factory: Callable[[str], OkLine] = OkLine.from_tokens_file,
    ) -> None:
        self._session_path = session_path
        self._api_factory = api_factory
        self._lock = threading.RLock()
        self._call_lock = threading.Lock()
        self._api: OkLine | None = None
        self._ready: Ready | None = None
        self._session_mtime: float | None = None
        self._profile: dict[str, Any] | None = None
        self._directory: list[dict[str, Any]] | None = None
        self._directory_at = 0.0
        self._names: dict[str, str] | None = None
        self._names_at = 0.0

    # -- lifecycle ----------------------------------------------------------
    def ensure_ready(self) -> Ready:
        """Cheap, cached precondition check; every read tool calls this first.

        Raises `ToolError` with the next step instead of letting a raw library
        error escape. Never opens a window and never spends a network call.
        """
        with self._lock:
            path = self._session_path()
            if not path.exists():
                raise ToolError(f"No LINE session yet at {path}. {LOGIN_HINT}")

            mtime = path.stat().st_mtime
            if self._ready is not None and self._api is not None and mtime == self._session_mtime:
                return self._ready

            if not LtsmBridge.is_available():
                raise ToolError(f"Node.js is required to sign LINE requests. {NODE_HINT}")

            self.close()
            try:
                api = self._api_factory(str(path))
                ready = Ready(
                    session_path=path,
                    e2ee_ready=bool(api.e2ee.is_ready()),
                    node_available=True,
                )
            except Exception as exc:  # noqa: BLE001 - any failure means "unusable"
                raise ToolError(
                    f"The LINE session at {path} could not be opened ({exc}). {SESSION_BROKEN_HINT}"
                ) from exc

            self._api = api
            self._ready = ready
            self._session_mtime = mtime
            return ready

    def close(self) -> None:
        """Drop the client (and its Node subprocess) plus every cache."""
        with self._lock:
            if self._api is not None:
                with contextlib.suppress(Exception):  # closing must never fail a call
                    self._api.close()
            self._api = None
            self._ready = None
            self._session_mtime = None
            self._profile = None
            self._directory = None
            self._directory_at = 0.0
            self._names = None
            self._names_at = 0.0

    def invalidate(self) -> None:
        """Forget the client after LINE rejected the token."""
        self.close()

    def revoke(self) -> dict[str, Any]:
        """Invalidate this device's session on LINE; never raises.

        `remote` is "revoked" when LINE accepted the logout, "skipped" when
        there is no usable client or token to ask, and "failed" with `error`
        set when the request itself died. The caller deletes the local file
        either way: that file is the real risk, not the server's copy.
        """
        try:
            self.ensure_ready()
        except ToolError as exc:
            return {"remote": "skipped", "error": str(exc)}

        with self._lock:
            api = self._api
        tokens = getattr(api, "tokens", None)
        if api is None or not getattr(tokens, "access_token", None):
            return {"remote": "skipped", "error": None}
        try:
            self._call(lambda api: api.auth.logout())
        except ToolError as exc:
            return {"remote": "failed", "error": str(exc)}
        return {"remote": "revoked", "error": None}

    def shutdown(self) -> None:
        """Close the client without cutting a request in half.

        `close()` alone can run while another tool call is mid-request; taking
        the call lock first waits for it. `invalidate()` must keep calling
        `close()` directly, because `_call` already holds that lock when a
        session is rejected.
        """
        with self._call_lock:
            self.close()

    # -- calls --------------------------------------------------------------
    def _call(self, operation: Callable[[OkLine], Any]) -> Any:
        with self._lock:
            api = self._api
        if api is None:
            self.ensure_ready()
            api = self._api
        # The lock covers the request itself: requests.Session and the Node
        # signer are not thread safe, and FastMCP runs sync tools in a worker
        # pool, so two tool calls can otherwise share one client mid-flight.
        with self._call_lock:
            try:
                return operation(api)  # type: ignore[arg-type]
            except ToolError:
                raise
            except HmacSignerError as exc:
                raise ToolError(f"{exc} {NODE_HINT}") from exc
            except (LineAuthError, LineTransportError) as exc:
                self.invalidate()
                raise ToolError(
                    f"LINE refused the session ({exc}). Call login again, then retry."
                ) from exc
            except LineError as exc:
                raise ToolError(f"LINE request failed: {exc}") from exc

    # -- data ---------------------------------------------------------------
    def profile(self) -> dict[str, Any]:
        with self._lock:
            if self._profile is not None:
                return dict(self._profile)
        raw = self._call(lambda api: api.get_profile()) or {}
        profile = {
            "mid": raw.get("mid"),
            "display_name": raw.get("displayName"),
        }
        with self._lock:
            self._profile = profile
        return dict(profile)

    def contact_names(self, *, refresh: bool = False) -> dict[str, str]:
        """`{mid: display name}` for every contact, cached for a few minutes."""
        with self._lock:
            if (
                not refresh
                and self._names is not None
                and time.monotonic() - self._names_at < CACHE_TTL_SEC
            ):
                return dict(self._names)

        ids = [str(mid) for mid in (self._call(lambda api: api.get_all_contact_ids()) or [])]
        names: dict[str, str] = {}
        for start in range(0, len(ids), CONTACT_BATCH):
            self._merge_contacts(ids[start : start + CONTACT_BATCH], names)
        self._store_names(names)
        return dict(names)

    def resolve_names(self, mids: Iterable[str]) -> dict[str, str]:
        """Names for arbitrary mids, including group members who are not contacts.

        LINE does not put display names in group messages, and `getChats` returns
        member mids only, so the sender mids of a group chat have to be looked up
        directly. Only the mids that are still unknown are fetched.
        """
        names = self.contact_names()
        missing = [mid for mid in dict.fromkeys(str(m) for m in mids if m) if mid not in names]
        if not missing:
            return names
        for start in range(0, len(missing), CONTACT_BATCH):
            self._merge_contacts(missing[start : start + CONTACT_BATCH], names)
        self._store_names(names)
        return dict(names)

    def _merge_contacts(self, batch: list[str], names: dict[str, str]) -> None:
        result = self._call(partial(_fetch_contacts, batch)) or {}
        for mid, wrapper in (result.get("contacts") or {}).items():
            contact = wrapper.get("contact", wrapper) if isinstance(wrapper, dict) else {}
            name = contact.get("displayNameOverridden") or contact.get("displayName")
            if name:
                names[str(mid)] = name

    def _store_names(self, names: dict[str, str]) -> None:
        with self._lock:
            self._names = dict(names)
            self._names_at = time.monotonic()

    def directory(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Chats with name, kind, unread count and a one-line preview."""
        with self._lock:
            if (
                not refresh
                and self._directory is not None
                and time.monotonic() - self._directory_at < CACHE_TTL_SEC
            ):
                return [dict(chat) for chat in self._directory]

        boxes = self._call(
            lambda api: api.get_message_boxes(
                limit=100,
                active_only=True,
                unread_only=False,
                with_unread_count=True,
                last_messages_per_box=1,
            )
        )
        entries = boxes.get("messageBoxes") if isinstance(boxes, dict) else None
        entries = entries if isinstance(entries, list) else []

        mids = [str(box.get("id")) for box in entries if box.get("id")]
        chats = self._chats_by_mid(mids)
        me = str(self.profile().get("mid") or "")
        # LINE leaves `chatName` empty on a direct chat (and often on a room),
        # so the peer mids are resolved up front. Contacts cover most of them;
        # only the rest costs a request.
        names = self.resolve_names(_unnamed_peers(chats, me))

        directory: list[dict[str, Any]] = []
        for box in entries:
            mid = str(box.get("id") or "")
            if not mid:
                continue
            chat = chats.get(mid) or {}
            last = _first_message(box)
            name = _chat_name(chat) or _peer_name(chat, mid, names, me)
            if not name:
                sender = str(last.get("from") or "")
                if sender and sender != me:
                    name = names.get(sender) or ""
            directory.append(
                {
                    "mid": mid,
                    "name": name or f"Chat {mid[:6]}",
                    "kind": _chat_kind(chat.get("type"), mid),
                    "unread": int(box.get("unreadCount") or 0),
                    "last_ms": _message_ms(last) or None,
                    "last_preview": self._preview(last, names),
                }
            )

        with self._lock:
            self._directory = directory
            self._directory_at = time.monotonic()
        return [dict(chat) for chat in directory]

    def recent(self, chat_mid: str, count: int) -> list[dict[str, Any]]:
        """Newest messages first, as LINE returns them."""
        result = self._call(lambda api: api.get_recent_messages(chat_mid, count))
        return _as_messages(result)

    def previous(
        self, chat_mid: str, end_message_id: str, delivered_ms: int, count: int
    ) -> list[dict[str, Any]]:
        """One older page, newest first."""
        result = self._call(
            lambda api: api.get_previous_messages(
                chat_mid, end_message_id, delivered_ms, count=count
            )
        )
        return _as_messages(result)

    def decrypt(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Open a Letter-Sealed message; failures leave it sealed."""
        with self._lock:
            api = self._api
        if api is None or not message.get("chunks"):
            return dict(message)
        try:
            return dict(api.decrypt_message(dict(message)))
        except Exception:  # noqa: BLE001 - a bad key must not kill the list
            return dict(message)

    def download(self, message: Mapping[str, Any]) -> bytes | None:
        """Fetch an image's bytes from OBS, or None when it is not reachable."""
        meta = message.get("contentMetadata")
        meta = meta if isinstance(meta, Mapping) else {}
        service = str(meta.get("serviceCode") or "talk")
        sid = str(meta.get("sid") or "m")
        oid = str(meta.get("objectId") or message.get("id") or "")
        if not oid:
            return None
        try:
            return self._call(lambda api: api.obs.download_object(service, sid, oid))
        except ToolError:
            # One unreadable image must not fail the whole listing.
            return None

    # -- helpers ------------------------------------------------------------
    def _chats_by_mid(self, mids: list[str]) -> dict[str, dict[str, Any]]:
        if not mids:
            return {}
        result = self._call(lambda api: api.get_chats(mids, with_members=True)) or {}
        chats = result.get("chats") if isinstance(result, dict) else None
        return {
            str(chat.get("chatMid")): chat
            for chat in (chats if isinstance(chats, list) else [])
            if chat.get("chatMid")
        }

    def _preview(self, message: Mapping[str, Any], names: Mapping[str, str]) -> str | None:
        if not message:
            return None
        view = to_view(message, names, self.decrypt)
        text = view.get("text") or view.get("summary")
        if not text:
            return None
        return text[:120]


def _fetch_contacts(batch: list[str], api: OkLine) -> Any:
    return api.get_contacts(batch)


def _as_messages(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, list):
        return []
    return [dict(item) for item in result if isinstance(item, Mapping)]


def _first_message(box: Mapping[str, Any]) -> dict[str, Any]:
    messages = box.get("lastMessages")
    if isinstance(messages, list) and messages and isinstance(messages[0], Mapping):
        return dict(messages[0])
    return {}


def _message_ms(message: Mapping[str, Any]) -> int:
    try:
        return int(message.get("deliveredTime") or message.get("createdTime") or 0)
    except (TypeError, ValueError):
        return 0


def _chat_name(chat: Mapping[str, Any]) -> str:
    return str(chat.get("chatName") or "").strip()


def _unnamed_peers(chats: Mapping[str, Mapping[str, Any]], me: str) -> list[str]:
    """Mids that could name the chats LINE returned without a `chatName`."""
    peers: list[str] = []
    for mid, chat in chats.items():
        if _chat_name(chat):
            continue
        peers.extend(_peer_mids(chat, mid, me))
    return peers


def _peer_mids(chat: Mapping[str, Any], chat_mid: str, me: str) -> list[str]:
    """Candidate mids for one chat: its members, or the other side of a DM.

    Groups keep their members in `extra.groupExtra.memberMids`; rooms and direct
    chats carry a top-level `memberMids`. A direct chat with neither is named
    after the mid itself, because LINE uses the peer's user mid as the chat mid.
    """
    members = chat.get("memberMids")
    if not isinstance(members, list):
        extra = chat.get("extra")
        group = extra.get("groupExtra") if isinstance(extra, Mapping) else None
        members = group.get("memberMids") if isinstance(group, Mapping) else None
    peers = [str(mid) for mid in (members or []) if mid and str(mid) != me]
    if not peers and chat_mid[:1].lower() == "u":
        # A direct chat's mid is the other user's mid. Real mids start with an
        # uppercase `U`, the archived ones this was first written against did not.
        peers = [chat_mid]
    return peers


def _peer_name(chat: Mapping[str, Any], chat_mid: str, names: Mapping[str, str], me: str) -> str:
    for mid in _peer_mids(chat, chat_mid, me):
        name = names.get(mid)
        if name:
            return name
    return ""


def _chat_kind(chat_type: Any, mid: str) -> str:
    """LINE's chat type, with a mid-prefix fallback for chats that were not listed."""
    try:
        value = int(chat_type)
    except (TypeError, ValueError):
        value = -1
    if value == 0:
        return "group"
    if value == 1:
        return "room"
    if value == 2:
        return "dm"
    head = (mid or "")[:1].lower()
    return {"c": "group", "r": "room", "u": "dm"}.get(head, "dm")


__all__ = ["LineReader", "Ready", "kind_name"]
