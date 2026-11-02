"""Local SQLite archive of chats, messages and downloaded media.

Single responsibility: storage. Nothing here talks to LINE, and nothing here
decides what a message means -- callers pass rows produced by `view`.

The database is the reason this server is not read-only toward the machine:
it keeps a plaintext copy of every message it has served, plus a backup of
every image. See SECURITY.md.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from line_desktop_mcp import media, paths

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    mid        TEXT PRIMARY KEY,
    name       TEXT,
    kind       TEXT,
    unread     INTEGER NOT NULL DEFAULT 0,
    last_ms    INTEGER,
    updated_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    chat_mid   TEXT NOT NULL,
    from_mid   TEXT,
    from_name  TEXT,
    delivered_ms INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    text       TEXT,
    sealed     INTEGER NOT NULL DEFAULT 0,
    content_type INTEGER NOT NULL DEFAULT 0,
    raw_json   TEXT,
    fetched_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_time
    ON messages(chat_mid, delivered_ms DESC);

CREATE TABLE IF NOT EXISTS media (
    message_id TEXT PRIMARY KEY,
    chat_mid   TEXT NOT NULL,
    rel_path   TEXT NOT NULL,
    ext        TEXT NOT NULL,
    bytes      INTEGER NOT NULL,
    sha256     TEXT NOT NULL,
    sealed     INTEGER NOT NULL DEFAULT 0,
    saved_ms   INTEGER NOT NULL
);
"""


class Archive:
    """One SQLite connection guarded by a lock, shared by every tool."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.archive_path()
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # -- plumbing -----------------------------------------------------------
    def _connection(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=5000")
                self._migrate(conn)
                self._conn = conn
            return self._conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version >= SCHEMA_VERSION:
            return
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # -- chats --------------------------------------------------------------
    def upsert_chats(self, chats: Iterable[Mapping[str, Any]]) -> None:
        """Save a chat listing.

        The `WHERE` keeps an unchanged listing a read instead of a write: read
        tools persist the directory on every call, and rewriting a hundred rows
        each time would only churn the WAL.
        """
        rows = [
            (
                str(chat.get("mid") or ""),
                chat.get("name"),
                chat.get("kind"),
                int(chat.get("unread") or 0),
                int(chat.get("last_ms") or 0) or None,
                int(time.time() * 1000),
            )
            for chat in chats
            if chat.get("mid")
        ]
        if not rows:
            return
        with self._lock:
            conn = self._connection()
            conn.executemany(
                """
                INSERT INTO chats (mid, name, kind, unread, last_ms, updated_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(mid) DO UPDATE SET
                    name    = COALESCE(NULLIF(excluded.name, ''), chats.name),
                    kind    = COALESCE(excluded.kind, chats.kind),
                    unread  = excluded.unread,
                    last_ms = COALESCE(excluded.last_ms, chats.last_ms),
                    updated_ms = excluded.updated_ms
                WHERE chats.name    IS NOT COALESCE(NULLIF(excluded.name, ''), chats.name)
                   OR chats.kind    IS NOT COALESCE(excluded.kind, chats.kind)
                   OR chats.unread  IS NOT excluded.unread
                   OR chats.last_ms IS NOT COALESCE(excluded.last_ms, chats.last_ms)
                """,
                rows,
            )
            conn.commit()

    def chats(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._connection().execute(
                "SELECT mid, name, kind, unread, last_ms FROM chats ORDER BY last_ms DESC"
            )
            return [dict(row) for row in cur.fetchall()]

    # -- messages -----------------------------------------------------------
    def upsert_messages(self, rows: Iterable[Mapping[str, Any]]) -> None:
        now = int(time.time() * 1000)
        values = [
            (
                str(row.get("id") or ""),
                str(row.get("chat_mid") or ""),
                row.get("from_mid"),
                row.get("from_name"),
                int(row.get("delivered_ms") or 0),
                str(row.get("kind") or "text"),
                row.get("text"),
                int(row.get("sealed") or 0),
                int(row.get("content_type") or 0),
                row.get("raw_json"),
                now,
            )
            for row in rows
            if row.get("id") and row.get("chat_mid")
        ]
        if not values:
            return
        with self._lock:
            conn = self._connection()
            conn.executemany(
                """
                INSERT INTO messages (
                    id, chat_mid, from_mid, from_name, delivered_ms, kind,
                    text, sealed, content_type, raw_json, fetched_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    text      = COALESCE(excluded.text, messages.text),
                    sealed    = CASE WHEN messages.sealed = 0 THEN 0 ELSE excluded.sealed END,
                    from_name = COALESCE(NULLIF(excluded.from_name, ''), messages.from_name),
                    raw_json  = excluded.raw_json,
                    fetched_ms = excluded.fetched_ms
                """,
                values,
            )
            conn.commit()

    def messages(
        self, chat_mid: str, limit: int, before_ms: int | None = None
    ) -> list[dict[str, Any]]:
        """Newest first, capped at `limit`, optionally before a timestamp."""
        sql = (
            "SELECT id, chat_mid, from_mid, from_name, delivered_ms, kind, text, "
            "sealed, content_type, raw_json FROM messages WHERE chat_mid = ?"
        )
        params: list[Any] = [chat_mid]
        if before_ms is not None:
            sql += " AND delivered_ms < ?"
            params.append(int(before_ms))
        sql += " ORDER BY delivered_ms DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            cur = self._connection().execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def oldest(self, chat_mid: str) -> dict[str, Any] | None:
        """Oldest archived message of one chat, used as a paging cursor."""
        with self._lock:
            row = (
                self._connection()
                .execute(
                    "SELECT id, delivered_ms FROM messages WHERE chat_mid = ? "
                    "ORDER BY delivered_ms ASC LIMIT 1",
                    (chat_mid,),
                )
                .fetchone()
            )
            return dict(row) if row else None

    def coverage(
        self,
        *,
        chat_mids: list[str] | None = None,
        after_ms: int | None = None,
        before_ms: int | None = None,
        from_mid: str | None = None,
    ) -> dict[str, int]:
        """How much a search would look at, and what it cannot read.

        `scanned` counts the rows the text match can actually see; `in_scope`
        counts every row the filters select, so the difference is what the
        search silently skips (images, stickers, sealed messages).
        """
        where, params = _filters("", chat_mids, after_ms, before_ms, from_mid)
        sql = (
            "SELECT COUNT(*) AS in_scope, "
            "SUM(text IS NOT NULL) AS scanned, "
            "SUM(sealed = 1) AS sealed "
            "FROM messages WHERE 1=1"
        )
        with self._lock:
            row = self._connection().execute(sql + where, params).fetchone()
        return {
            "in_scope": int(row["in_scope"] or 0),
            "scanned": int(row["scanned"] or 0),
            "sealed": int(row["sealed"] or 0),
        }

    def senders(self, chat_mids: list[str] | None = None) -> list[dict[str, Any]]:
        """Senders already archived, most recent name first.

        One mid can appear more than once after a rename; the first row for a
        mid is the name of their latest message, which is the one a user
        recognises.
        """
        sql = (
            "SELECT from_mid, from_name, COUNT(*) AS hits, MAX(delivered_ms) AS last_ms "
            "FROM messages WHERE from_mid IS NOT NULL"
        )
        params: list[Any] = []
        if chat_mids:
            sql += f" AND chat_mid IN ({','.join('?' * len(chat_mids))})"
            params.extend(chat_mids)
        sql += " GROUP BY from_mid, from_name ORDER BY last_ms DESC, hits DESC"
        with self._lock:
            cur = self._connection().execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def search(
        self,
        text: str,
        *,
        limit: int,
        offset: int = 0,
        chat_mids: list[str] | None = None,
        after_ms: int | None = None,
        before_ms: int | None = None,
        from_mid: str | None = None,
        newest_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Case-insensitive substring search over archived text."""
        sql = (
            "SELECT m.id, m.chat_mid, m.from_mid, m.from_name, m.delivered_ms, "
            "m.kind, m.text, m.sealed, m.content_type, m.raw_json, "
            "c.name AS chat_name "
            "FROM messages m LEFT JOIN chats c ON c.mid = m.chat_mid "
            "WHERE m.text IS NOT NULL AND m.text LIKE ? ESCAPE '\\'"
        )
        params: list[Any] = [f"%{_escape_like(text)}%"]
        where, more = _filters("m.", chat_mids, after_ms, before_ms, from_mid)
        sql += where
        params.extend(more)
        # The id breaks ties so paging stays stable for messages sharing a
        # millisecond, which LINE does send in bursts.
        direction = "DESC" if newest_first else "ASC"
        sql += f" ORDER BY m.delivered_ms {direction}, m.id {direction} LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])
        with self._lock:
            cur = self._connection().execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def known_ids(self, message_ids: Iterable[str]) -> set[str]:
        """Which of these ids are already archived."""
        ids = [str(mid) for mid in message_ids if mid]
        if not ids:
            return set()
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            cur = self._connection().execute(
                f"SELECT id FROM messages WHERE id IN ({placeholders})", ids
            )
            return {str(row[0]) for row in cur.fetchall()}

    # -- media --------------------------------------------------------------
    def media_path(self, chat_mid: str, message_id: str, ext: str) -> Path:
        """Absolute path for one media file, inside the media directory only."""
        root = paths.media_dir()
        folder = root / media.safe_component(chat_mid)
        target = folder / f"{media.safe_component(message_id)}.{media.safe_component(ext)}"
        if root.resolve() not in target.resolve().parents:
            raise ValueError(f"media path escaped the media directory: {target}")
        return target

    def record_media(
        self, message_id: str, chat_mid: str, path: Path, data: bytes, ext: str
    ) -> dict[str, Any]:
        rel = path.relative_to(paths.media_dir()).as_posix()
        digest = media.sha256(data)
        with self._lock:
            conn = self._connection()
            conn.execute(
                """
                INSERT INTO media (message_id, chat_mid, rel_path, ext, bytes, sha256, sealed, saved_ms)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    rel_path = excluded.rel_path,
                    ext = excluded.ext,
                    bytes = excluded.bytes,
                    sha256 = excluded.sha256,
                    saved_ms = excluded.saved_ms
                """,
                (message_id, chat_mid, rel, ext, len(data), digest, int(time.time() * 1000)),
            )
            conn.commit()
        return {"message_id": message_id, "rel_path": rel, "bytes": len(data), "ext": ext}

    def media_for(self, message_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = [str(mid) for mid in message_ids if mid]
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            cur = self._connection().execute(
                f"SELECT message_id, chat_mid, rel_path, ext, bytes, sha256 "
                f"FROM media WHERE message_id IN ({placeholders})",
                ids,
            )
            return {str(row["message_id"]): dict(row) for row in cur.fetchall()}


def _filters(
    prefix: str,
    chat_mids: list[str] | None,
    after_ms: int | None,
    before_ms: int | None,
    from_mid: str | None,
) -> tuple[str, list[Any]]:
    """The `WHERE` fragment shared by a search and its coverage count."""
    sql = ""
    params: list[Any] = []
    if chat_mids:
        sql += f" AND {prefix}chat_mid IN ({','.join('?' * len(chat_mids))})"
        params.extend(chat_mids)
    if after_ms is not None:
        sql += f" AND {prefix}delivered_ms >= ?"
        params.append(int(after_ms))
    if before_ms is not None:
        sql += f" AND {prefix}delivered_ms < ?"
        params.append(int(before_ms))
    if from_mid:
        sql += f" AND {prefix}from_mid = ?"
        params.append(from_mid)
    return sql, params


def _escape_like(text: str) -> str:
    """Neutralise LIKE wildcards so a search for "100%" matches the literal text."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
