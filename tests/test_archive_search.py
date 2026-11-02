"""Archive search: substring matching, escaping, scoping, counting."""

from __future__ import annotations

from pathlib import Path

import pytest

from line_desktop_mcp.archive import Archive


@pytest.fixture()
def archive(tmp_path: Path) -> Archive:
    store = Archive(tmp_path / "archive.db")
    yield store
    store.close()


def _row(message_id: str, text: str, chat: str = "c1", ms: int = 1_000) -> dict:
    return {
        "id": message_id,
        "chat_mid": chat,
        "from_mid": "u1",
        "from_name": "Soda",
        "delivered_ms": ms,
        "kind": "text",
        "text": text,
        "sealed": 0,
        "content_type": 0,
        "raw_json": "{}",
    }


def test_search_finds_a_substring_ignoring_ascii_case(archive: Archive) -> None:
    archive.upsert_messages([_row("m1", "Please send the INVOICE today")])

    hits = archive.search("invoice", limit=10)

    assert [row["id"] for row in hits] == ["m1"]


def test_search_matches_thai_text_without_tokenisation(archive: Archive) -> None:
    archive.upsert_messages([_row("m1", "แอร์ห้อง 4/6 อาการยังเหมือนเดิม")])

    assert len(archive.search("แอร์ห้อง", limit=10)) == 1
    assert archive.search("เครื่องซักผ้า", limit=10) == []


def test_search_treats_wildcards_as_literal_text(archive: Archive) -> None:
    archive.upsert_messages([_row("m1", "progress 100% done"), _row("m2", "a_b value")])

    assert [row["id"] for row in archive.search("100%", limit=10)] == ["m1"]
    assert [row["id"] for row in archive.search("a_b", limit=10)] == ["m2"]
    assert archive.search("a%b", limit=10) == []


def test_search_orders_newest_first(archive: Archive) -> None:
    archive.upsert_messages(
        [
            _row("m1", "ticket", ms=1_000),
            _row("m2", "ticket", ms=3_000),
            _row("m3", "ticket", ms=2_000),
        ]
    )

    assert [row["id"] for row in archive.search("ticket", limit=10)] == ["m2", "m3", "m1"]


def test_search_respects_the_limit(archive: Archive) -> None:
    archive.upsert_messages([_row(f"m{i}", "ticket", ms=1_000 + i) for i in range(5)])

    assert len(archive.search("ticket", limit=2)) == 2


def test_search_can_be_scoped_to_chats(archive: Archive) -> None:
    archive.upsert_messages([_row("m1", "ticket", chat="c1"), _row("m2", "ticket", chat="c2")])

    hits = archive.search("ticket", limit=10, chat_mids=["c2"])

    assert [row["id"] for row in hits] == ["m2"]


def test_search_can_be_limited_by_time(archive: Archive) -> None:
    archive.upsert_messages([_row("m1", "ticket", ms=1_000), _row("m2", "ticket", ms=5_000)])

    hits = archive.search("ticket", limit=10, since_ms=3_000)

    assert [row["id"] for row in hits] == ["m2"]


def test_search_returns_the_chat_name(archive: Archive) -> None:
    archive.upsert_chats([{"mid": "c1", "name": "Work Team", "kind": "group", "unread": 0}])
    archive.upsert_messages([_row("m1", "ticket")])

    (hit,) = archive.search("ticket", limit=10)

    assert hit["chat_name"] == "Work Team"


def test_search_ignores_messages_without_text(archive: Archive) -> None:
    archive.upsert_messages([_row("m1", None), _row("m2", "ticket")])

    assert [row["id"] for row in archive.search("ticket", limit=10)] == ["m2"]


def test_count_matches_what_search_would_scan(archive: Archive) -> None:
    archive.upsert_messages(
        [_row("m1", "a", chat="c1", ms=1_000), _row("m2", "b", chat="c2", ms=5_000)]
    )

    assert archive.count() == 2
    assert archive.count(["c1"]) == 1
    assert archive.count(since_ms=3_000) == 1


def test_oldest_returns_the_earliest_message(archive: Archive) -> None:
    archive.upsert_messages([_row("m1", "a", ms=5_000), _row("m2", "b", ms=1_000)])

    oldest = archive.oldest("c1")

    assert oldest is not None
    assert oldest["id"] == "m2"


def test_oldest_of_an_unknown_chat_is_none(archive: Archive) -> None:
    assert archive.oldest("nope") is None
