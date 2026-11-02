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


def _row(
    message_id: str,
    text: str,
    chat: str = "c1",
    ms: int = 1_000,
    sender: str = "u1",
    sealed: int = 0,
) -> dict:
    return {
        "id": message_id,
        "chat_mid": chat,
        "from_mid": sender,
        "from_name": f"name-{sender}",
        "delivered_ms": ms,
        "kind": "text",
        "text": text,
        "sealed": sealed,
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

    assert [row["id"] for row in archive.search("ticket", limit=10, after_ms=3_000)] == ["m2"]
    assert [row["id"] for row in archive.search("ticket", limit=10, before_ms=3_000)] == ["m1"]


def test_search_can_be_limited_by_sender(archive: Archive) -> None:
    archive.upsert_messages([_row("m1", "ticket", sender="u1"), _row("m2", "ticket", sender="u2")])

    hits = archive.search("ticket", limit=10, from_mid="u2")

    assert [row["id"] for row in hits] == ["m2"]


def test_search_can_return_oldest_first(archive: Archive) -> None:
    archive.upsert_messages([_row("m1", "ticket", ms=1_000), _row("m2", "ticket", ms=2_000)])

    hits = archive.search("ticket", limit=10, newest_first=False)

    assert [row["id"] for row in hits] == ["m1", "m2"]


def test_search_can_skip_hits_for_paging(archive: Archive) -> None:
    archive.upsert_messages([_row(f"m{i}", "ticket", ms=1_000 + i) for i in range(3)])

    page = archive.search("ticket", limit=1, offset=1)

    assert [row["id"] for row in page] == ["m1"]


def test_search_returns_the_chat_name(archive: Archive) -> None:
    archive.upsert_chats([{"mid": "c1", "name": "Work Team", "kind": "group", "unread": 0}])
    archive.upsert_messages([_row("m1", "ticket")])

    (hit,) = archive.search("ticket", limit=10)

    assert hit["chat_name"] == "Work Team"


def test_search_ignores_messages_without_text(archive: Archive) -> None:
    archive.upsert_messages([_row("m1", None), _row("m2", "ticket")])

    assert [row["id"] for row in archive.search("ticket", limit=10)] == ["m2"]


def test_coverage_separates_scanned_from_in_scope(archive: Archive) -> None:
    archive.upsert_messages(
        [
            _row("m1", "ticket", ms=5_000),
            _row("m2", None, ms=1_000),
            _row("m3", None, ms=2_000, sealed=1),
        ]
    )

    assert archive.coverage() == {"in_scope": 3, "scanned": 1, "sealed": 1}
    assert archive.coverage(chat_mids=["c2"]) == {"in_scope": 0, "scanned": 0, "sealed": 0}
    assert archive.coverage(after_ms=3_000) == {"in_scope": 1, "scanned": 1, "sealed": 0}
    assert archive.coverage(from_mid="u9") == {"in_scope": 0, "scanned": 0, "sealed": 0}


def test_oldest_returns_the_earliest_message(archive: Archive) -> None:
    archive.upsert_messages([_row("m1", "a", ms=5_000), _row("m2", "b", ms=1_000)])

    oldest = archive.oldest("c1")

    assert oldest is not None
    assert oldest["id"] == "m2"


def test_oldest_of_an_unknown_chat_is_none(archive: Archive) -> None:
    assert archive.oldest("nope") is None
