"""Shared test doubles: a tool collector and a fake LINE reader."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from line_desktop_mcp.reader import Ready


class StubMCP:
    """Collects `@mcp.tool` functions so tests can call them directly."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}
        self.annotations: dict[str, Any] = {}

    def tool(
        self,
        *,
        name: str | None = None,
        title: str | None = None,
        tags: set[str] | None = None,
        annotations: Any = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[name or function.__name__] = function
            self.annotations[name or function.__name__] = annotations
            return function

        return decorate


class FakeReader:
    """Stands in for LineReader; records the order of what it was asked."""

    def __init__(
        self,
        *,
        chats: Any = None,
        recent: Any = None,
        previous: Mapping[str, list[dict[str, Any]]] | None = None,
        names: Mapping[str, str] | None = None,
        download: Callable[[Mapping[str, Any]], bytes | None] | None = None,
        e2ee_ready: bool = True,
        revoke_result: Mapping[str, Any] | None = None,
        revoke_error: Exception | None = None,
    ) -> None:
        self.chats = chats if chats is not None else []
        self._recent = recent if recent is not None else []
        self._previous = dict(previous or {})
        self._names = dict(names or {"u1": "Soda"})
        self._download = download
        self.revoke_result = dict(revoke_result or {"remote": "revoked", "error": None})
        self.revoke_error = revoke_error
        self.ready = Ready(Path("session.json"), e2ee_ready, True)
        self.calls: list[Any] = []
        self.downloaded: list[str] = []

    def ensure_ready(self) -> Ready:
        self.calls.append("ensure_ready")
        return self.ready

    def directory(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        self.calls.append(("directory", refresh))
        if isinstance(self.chats, Exception):
            raise self.chats
        return [dict(chat) for chat in self.chats]

    def contact_names(self, *, refresh: bool = False) -> dict[str, str]:
        return dict(self._names)

    def resolve_names(self, mids) -> dict[str, str]:
        self.calls.append(("resolve_names", len(list(mids))))
        return dict(self._names)

    def recent(self, chat_mid: str, count: int) -> list[dict[str, Any]]:
        self.calls.append(("recent", chat_mid, count))
        if isinstance(self._recent, Exception):
            raise self._recent
        return [dict(message) for message in self._recent]

    def previous(
        self, chat_mid: str, end_message_id: str, delivered_ms: int, count: int
    ) -> list[dict[str, Any]]:
        self.calls.append(("previous", end_message_id))
        return [dict(message) for message in self._previous.get(end_message_id, [])]

    def decrypt(self, message: Mapping[str, Any]) -> dict[str, Any]:
        return dict(message)

    def revoke(self) -> dict[str, Any]:
        self.calls.append("revoke")
        if self.revoke_error is not None:
            raise self.revoke_error
        return dict(self.revoke_result)

    def shutdown(self) -> None:
        self.calls.append("shutdown")

    def download(self, message: Mapping[str, Any]) -> bytes | None:
        message_id = str(message.get("id") or "")
        self.downloaded.append(message_id)
        return self._download(message) if self._download else None
