"""The SQLite archive: upserts, ordering, chat metadata, media bookkeeping."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from line_desktop_mcp import paths
from line_desktop_mcp.archive import SCHEMA_VERSION, Archive


@pytest.fixture()
def archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Archive:
    monkeypatch.setenv("LINE_DESKTOP_MCP_MEDIA", str(tmp_path / "media"))
    store = Archive(tmp_path / "archive.db")
    yield store
    store.close()


def _row(**extra):
    row = {
        "id": "m1",
        "chat_mid": "c1",
        "from_mid": "u1",
        "from_name": "Soda",
        "delivered_ms": 1000,
        "kind": "text",
        "text": "hi",
        "sealed": 0,
        "content_type": 0,
        "raw_json": '{"id": "m1"}',
    }
    row.update(extra)
    return row


def test_schema_is_versioned(archive: Archive) -> None:
    version = archive._connection().execute("PRAGMA user_version").fetchone()[0]

    assert int(version) == SCHEMA_VERSION


def test_upserting_the_same_message_twice_keeps_one_row(archive: Archive) -> None:
    archive.upsert_messages([_row()])
    archive.upsert_messages([_row()])

    rows = archive.messages("c1", limit=10)

    assert len(rows) == 1
    assert rows[0]["text"] == "hi"


def test_a_later_plaintext_upgrades_a_sealed_row(archive: Archive) -> None:
    archive.upsert_messages([_row(text=None, sealed=1)])
    archive.upsert_messages([_row(text="opened", sealed=0)])

    (row,) = archive.messages("c1", limit=10)

    assert row["text"] == "opened"
    assert row["sealed"] == 0


def test_an_edited_message_replaces_the_archived_text(archive: Archive) -> None:
    archive.upsert_messages([_row(text="old text")])
    archive.upsert_messages([_row(text="new text")])

    (row,) = archive.messages("c1", limit=10)

    assert row["text"] == "new text"


def test_a_sealed_row_does_not_erase_known_text(archive: Archive) -> None:
    archive.upsert_messages([_row(text="opened", sealed=0)])
    archive.upsert_messages([_row(text=None, sealed=1)])

    (row,) = archive.messages("c1", limit=10)

    assert row["text"] == "opened"
    assert row["sealed"] == 0


def test_messages_come_back_newest_first_and_respect_the_cursor(archive: Archive) -> None:
    archive.upsert_messages(
        [
            _row(id="m1", delivered_ms=1000),
            _row(id="m2", delivered_ms=2000),
            _row(id="m3", delivered_ms=3000),
        ]
    )

    newest = archive.messages("c1", limit=2)
    older = archive.messages("c1", limit=2, before_ms=2000)

    assert [row["id"] for row in newest] == ["m3", "m2"]
    assert [row["id"] for row in older] == ["m1"]


def test_chat_upsert_keeps_a_known_name(archive: Archive) -> None:
    archive.upsert_chats([{"mid": "c1", "name": "Family", "kind": "group", "unread": 2}])
    archive.upsert_chats([{"mid": "c1", "name": None, "kind": "group", "unread": 0}])

    (chat,) = archive.chats()

    assert chat["name"] == "Family"
    assert chat["unread"] == 0


def test_chats_can_be_searched_case_insensitively(archive: Archive) -> None:
    archive.upsert_chats(
        [
            {"mid": "c1", "name": "Family", "kind": "group", "unread": 0},
            {"mid": "c2", "name": "Work Team", "kind": "group", "unread": 0},
        ]
    )

    assert [chat["mid"] for chat in archive.find_chats("WORK")] == ["c2"]


def test_media_is_stored_inside_the_media_directory(archive: Archive, tmp_path: Path) -> None:
    path = archive.media_path("../../etc", "../m1", "png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"data")

    assert paths.media_dir().resolve() in path.resolve().parents

    record = archive.record_media("m1", "c1", path, b"data", "png")
    assert record["rel_path"].endswith("m1.png")
    assert archive.media_for(["m1"])["m1"]["bytes"] == 4


def test_media_lookup_with_no_ids_returns_nothing(archive: Archive) -> None:
    assert archive.media_for([]) == {}


def test_archive_creates_its_parent_directory(tmp_path: Path) -> None:
    store = Archive(tmp_path / "deep" / "nested" / "archive.db")
    try:
        store.upsert_chats([{"mid": "c1", "name": "x", "kind": "dm", "unread": 0}])
        assert (tmp_path / "deep" / "nested" / "archive.db").exists()
    finally:
        store.close()


def test_wal_mode_is_enabled(archive: Archive) -> None:
    mode = archive._connection().execute("PRAGMA journal_mode").fetchone()[0]

    assert str(mode).lower() == "wal"


def test_rows_are_plain_dicts(tmp_path: Path) -> None:
    store = Archive(tmp_path / "a.db")
    try:
        store.upsert_messages([_row()])
        (row,) = store.messages("c1", limit=1)
        assert isinstance(row, dict)
        assert not isinstance(row, sqlite3.Row)
    finally:
        store.close()
