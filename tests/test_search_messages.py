"""`search_messages`: archive hits, scoping, and the opt-in LINE harvest."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from line_desktop_mcp.archive import Archive
from line_desktop_mcp.tools import search_messages as tool
from line_desktop_mcp.view import to_row
from stubs import FakeReader, StubMCP

FAMILY = {"mid": "c1", "name": "Family", "kind": "group", "unread": 0}
WORK = {"mid": "c2", "name": "Work Team", "kind": "group", "unread": 0}


def _msg(message_id: str, text: str, ms: int = 1_000, sender: str = "u1", ct: int = 0) -> dict:
    return {
        "id": message_id,
        "from": sender,
        "deliveredTime": ms,
        "contentType": ct,
        "text": text,
    }


def _at(year: int, month: int, day: int, hour: int = 12) -> int:
    return int(datetime(year, month, day, hour).timestamp() * 1000)


@pytest.fixture()
def store(tmp_path: Path) -> Archive:
    archive = Archive(tmp_path / "archive.db")
    yield archive
    archive.close()


def _call(reader: FakeReader, archive: Archive, **kwargs):
    mcp = StubMCP()
    tool.register(mcp, reader, archive)  # type: ignore[arg-type]
    return mcp.tools["search_messages"](**kwargs)


def test_finds_archived_text_and_names_the_chat(store: Archive) -> None:
    store.upsert_chats([FAMILY])
    store.upsert_messages([to_row(_msg("m1", "please send the invoice"), {"u1": "Soda"}, "c1")])

    result = _call(FakeReader(chats=[FAMILY]), store, query="invoice")

    assert result["returned"] == 1
    assert result["hits"][0]["chat"]["name"] == "Family"
    assert result["hits"][0]["message"]["text"] == "please send the invoice"
    assert result["chats_hit"] == [{"mid": "c1", "name": "Family", "hits": 1}]


def test_reports_coverage_and_no_api_work_by_default(store: Archive) -> None:
    store.upsert_messages([to_row(_msg("m1", "invoice"), {"u1": "Soda"}, "c1")])

    result = _call(FakeReader(chats=[FAMILY]), store, query="invoice")

    assert result["scope"] == "archive"
    assert result["api_pages"] == 0
    assert result["coverage"]["messages_scanned"] == 1
    assert result["coverage"]["messages_in_scope"] == 1
    assert result["coverage"]["chats_total"] == 1


def test_a_hit_is_named_even_when_no_listing_was_archived(store: Archive) -> None:
    """The search reads the directory first, so the join can label the hit."""
    store.upsert_messages([to_row(_msg("m1", "invoice"), {"u1": "Soda"}, "c1")])

    result = _call(FakeReader(chats=[FAMILY]), store, query="invoice")

    assert result["hits"][0]["chat"]["name"] == "Family"


def test_truncated_is_false_when_the_limit_covers_every_hit(store: Archive) -> None:
    store.upsert_messages([to_row(_msg("m1", "invoice"), {"u1": "Soda"}, "c1")])

    result = _call(FakeReader(chats=[FAMILY]), store, query="invoice", limit=1)

    assert result["returned"] == 1
    assert result["truncated"] is False


def test_truncated_counts_from_the_offset(store: Archive) -> None:
    store.upsert_messages(
        [
            to_row(_msg("m1", "invoice", ms=1_000), {"u1": "Soda"}, "c1"),
            to_row(_msg("m2", "invoice", ms=2_000), {"u1": "Soda"}, "c1"),
        ]
    )

    last = _call(FakeReader(chats=[FAMILY]), store, query="invoice", limit=1, offset=1)

    assert [hit["message"]["id"] for hit in last["hits"]] == ["m1"]
    assert last["truncated"] is False


def test_returns_nothing_when_the_text_is_absent(store: Archive) -> None:
    store.upsert_messages([to_row(_msg("m1", "hello"), {"u1": "Soda"}, "c1")])

    result = _call(FakeReader(chats=[FAMILY]), store, query="invoice")

    assert result["hits"] == []
    assert result["chats_hit"] == []


def test_scopes_the_search_to_the_named_chats(store: Archive) -> None:
    store.upsert_messages(
        [
            to_row(_msg("m1", "invoice"), {"u1": "Soda"}, "c1"),
            to_row(_msg("m2", "invoice"), {"u1": "Soda"}, "c2"),
        ]
    )

    result = _call(FakeReader(chats=[FAMILY, WORK]), store, query="invoice", chats=["Work Team"])

    assert [hit["message"]["id"] for hit in result["hits"]] == ["m2"]


def test_an_ambiguous_scope_lists_candidates(store: Archive) -> None:
    reader = FakeReader(chats=[FAMILY, {**FAMILY, "mid": "c9"}])

    with pytest.raises(ToolError, match="2 chats match"):
        _call(reader, store, query="invoice", chats=["Family"])


def test_an_unknown_scope_asks_for_the_listing(store: Archive) -> None:
    with pytest.raises(ToolError, match="No chat matches"):
        _call(FakeReader(chats=[FAMILY]), store, query="invoice", chats=["Nowhere"])


def test_an_empty_query_is_rejected(store: Archive) -> None:
    with pytest.raises(ToolError, match="Provide `query`"):
        _call(FakeReader(chats=[FAMILY]), store, query="   ")


def test_kinds_find_media_without_a_query(store: Archive) -> None:
    names = {"u1": "Soda"}
    store.upsert_messages(
        [
            to_row(_msg("m1", "invoice"), names, "c1"),
            to_row(_msg("m2", text="", ct=1), names, "c1"),
        ]
    )

    result = _call(FakeReader(chats=[FAMILY]), store, kinds=["image"])

    assert [hit["message"]["id"] for hit in result["hits"]] == ["m2"]
    assert result["filters"]["kinds"] == ["image"]
    # No text query, so every in-scope message is a candidate.
    assert result["coverage"]["messages_scanned"] == 1


def test_kinds_are_normalised_and_mix_with_a_query(store: Archive) -> None:
    names = {"u1": "Soda"}
    store.upsert_messages(
        [
            to_row(_msg("m1", "invoice"), names, "c1"),
            to_row(_msg("m2", "invoice photo", ct=1), names, "c1"),
        ]
    )

    result = _call(FakeReader(chats=[FAMILY]), store, query="invoice", kinds=["IMAGE"])

    assert [hit["message"]["id"] for hit in result["hits"]] == ["m2"]
    assert result["filters"]["kinds"] == ["image"]


def test_an_unknown_kind_is_rejected(store: Archive) -> None:
    with pytest.raises(ToolError, match="Unknown kind"):
        _call(FakeReader(chats=[FAMILY]), store, kinds=["videoo"])


def test_a_search_without_query_or_kinds_is_rejected(store: Archive) -> None:
    with pytest.raises(ToolError, match="Provide `query`, `kinds`, or both"):
        _call(FakeReader(chats=[FAMILY]), store)


def test_since_days_filters_old_hits(store: Archive, monkeypatch: pytest.MonkeyPatch) -> None:
    mine = 1_700_000_000_000
    store.upsert_messages(
        [
            to_row(_msg("old", "invoice", ms=mine - 10 * 86_400_000), {"u1": "Soda"}, "c1"),
            to_row(_msg("new", "invoice", ms=mine), {"u1": "Soda"}, "c1"),
        ]
    )
    monkeypatch.setattr(tool, "time", type("T", (), {"time": staticmethod(lambda: mine / 1000)}))

    result = _call(FakeReader(chats=[FAMILY]), store, query="invoice", since_days=2)

    assert [hit["message"]["id"] for hit in result["hits"]] == ["new"]


def test_sync_without_chats_is_refused(store: Archive) -> None:
    with pytest.raises(ToolError, match="needs `chats`"):
        _call(FakeReader(chats=[FAMILY]), store, query="invoice", sync=True)


def test_sync_harvests_older_messages_before_searching(store: Archive) -> None:
    reader = FakeReader(
        chats=[FAMILY],
        recent=[_msg("m2", "invoice B", ms=2_000), _msg("m1", "invoice A", ms=1_000)],
        previous={"m1": [_msg("m0", "invoice C", ms=500)]},
    )

    result = _call(reader, store, query="invoice", chats=["Family"], sync=True)

    assert result["scope"] == "archive+api"
    assert result["api_pages"] == 2  # the newest page, then one older page
    assert [hit["message"]["id"] for hit in result["hits"]] == ["m2", "m1", "m0"]


def test_sync_still_searches_when_line_has_nothing_more(store: Archive) -> None:
    store.upsert_messages([to_row(_msg("m1", "invoice"), {"u1": "Soda"}, "c1")])
    reader = FakeReader(chats=[FAMILY], previous={})

    result = _call(reader, store, query="invoice", chats=["Family"], sync=True)

    assert result["api_pages"] == 1  # the newest page, which brought nothing new
    assert result["returned"] == 1


def test_reports_the_filters_it_applied(store: Archive) -> None:
    store.upsert_messages([to_row(_msg("m1", "invoice"), {"u1": "Soda"}, "c1")])

    result = _call(FakeReader(chats=[FAMILY]), store, query="invoice", from_sender="Soda")

    assert result["filters"] == {
        "chats": None,
        "kinds": None,
        "after": None,
        "before": None,
        "from": {"mid": "u1", "name": "Soda"},
        "order": "newest",
        "match": "substring",
        "offset": 0,
    }


def test_a_date_window_includes_both_ends(store: Archive) -> None:
    names = {"u1": "Soda"}
    store.upsert_messages(
        [
            to_row(_msg("jul", "invoice", ms=_at(2026, 10, 31, 23)), names, "c1"),
            to_row(_msg("first", "invoice", ms=_at(2026, 11, 1, 0)), names, "c1"),
            to_row(_msg("last", "invoice", ms=_at(2026, 11, 30, 23)), names, "c1"),
            to_row(_msg("next", "invoice", ms=_at(2026, 12, 1, 0)), names, "c1"),
        ]
    )

    result = _call(
        FakeReader(chats=[FAMILY]),
        store,
        query="invoice",
        after="2026-11-01",
        before="2026-11-30",
    )

    assert [hit["message"]["id"] for hit in result["hits"]] == ["last", "first"]
    assert result["filters"]["after"] == "2026-11-01"
    assert result["filters"]["before"] == "2026-11-30"


def test_after_wins_over_since_days(store: Archive) -> None:
    store.upsert_messages([to_row(_msg("m1", "invoice"), {"u1": "Soda"}, "c1")])

    result = _call(
        FakeReader(chats=[FAMILY]),
        store,
        query="invoice",
        after="2030-01-01",
        since_days=3650,
    )

    assert result["hits"] == []


def test_a_bad_date_is_rejected(store: Archive) -> None:
    with pytest.raises(ToolError, match="must be a date"):
        _call(FakeReader(chats=[FAMILY]), store, query="invoice", after="01/11/2026")


def test_an_empty_window_is_rejected(store: Archive) -> None:
    with pytest.raises(ToolError, match="earlier than"):
        _call(
            FakeReader(chats=[FAMILY]),
            store,
            query="invoice",
            after="2026-11-30",
            before="2026-11-01",
        )


def test_since_days_must_stay_before_the_window_end(store: Archive) -> None:
    """`since_days` sets the lower bound after `after` was checked, so check again."""
    with pytest.raises(ToolError, match="since_days"):
        _call(
            FakeReader(chats=[FAMILY]),
            store,
            query="invoice",
            since_days=30,
            before="2020-01-01",
        )


def test_sender_can_be_a_name_or_a_mid(store: Archive) -> None:
    names = {"u1": "Soda", "u2": "Mint"}
    store.upsert_messages(
        [
            to_row(_msg("m1", "invoice", sender="u1"), names, "c1"),
            to_row(_msg("m2", "invoice", ms=2_000, sender="u2"), names, "c1"),
        ]
    )

    by_name = _call(FakeReader(chats=[FAMILY]), store, query="invoice", from_sender="Soda")
    by_mid = _call(FakeReader(chats=[FAMILY]), store, query="invoice", from_sender="u2")

    assert [hit["message"]["id"] for hit in by_name["hits"]] == ["m1"]
    assert [hit["message"]["id"] for hit in by_mid["hits"]] == ["m2"]


def test_an_ambiguous_sender_lists_candidates(store: Archive) -> None:
    store.upsert_messages(
        [
            to_row(_msg("m1", "invoice", sender="u1"), {"u1": "Soda"}, "c1"),
            to_row(_msg("m2", "invoice", sender="u2"), {"u2": "Soda"}, "c1"),
        ]
    )

    with pytest.raises(ToolError, match="2 senders match"):
        _call(FakeReader(chats=[FAMILY]), store, query="invoice", from_sender="Soda")


def test_an_unknown_sender_is_reported(store: Archive) -> None:
    with pytest.raises(ToolError, match="No archived sender"):
        _call(FakeReader(chats=[FAMILY]), store, query="invoice", from_sender="Nobody")


def test_order_and_offset_page_through_hits(store: Archive) -> None:
    store.upsert_messages(
        [to_row(_msg(f"m{i}", "invoice", ms=1_000 + i), {"u1": "Soda"}, "c1") for i in range(3)]
    )
    reader = FakeReader(chats=[FAMILY])

    newest = _call(reader, store, query="invoice", limit=2)
    oldest = _call(reader, store, query="invoice", limit=2, order="oldest")
    second = _call(reader, store, query="invoice", limit=1, offset=1)

    assert [hit["message"]["id"] for hit in newest["hits"]] == ["m2", "m1"]
    assert newest["truncated"] is True
    assert [hit["message"]["id"] for hit in oldest["hits"]] == ["m0", "m1"]
    assert [hit["message"]["id"] for hit in second["hits"]] == ["m1"]


def test_hits_carry_a_snippet_around_the_match(store: Archive) -> None:
    long_text = f"{'a' * 80} invoice {'b' * 80}"
    store.upsert_messages([to_row(_msg("m1", long_text), {"u1": "Soda"}, "c1")])

    result = _call(FakeReader(chats=[FAMILY]), store, query="invoice")

    hit = result["hits"][0]
    assert "invoice" in hit["snippet"]
    assert hit["snippet"].startswith("…")
    assert hit["snippet"].endswith("…")
    assert hit["message"]["text"] == long_text  # the full text is still there


def test_a_kind_only_search_has_no_snippet(store: Archive) -> None:
    store.upsert_messages([to_row(_msg("m1", text="", ct=1), {"u1": "Soda"}, "c1")])

    result = _call(FakeReader(chats=[FAMILY]), store, kinds=["image"])

    assert result["hits"][0]["snippet"] is None


def test_words_find_terms_that_are_not_next_to_each_other(store: Archive) -> None:
    names = {"u1": "Soda"}
    store.upsert_messages(
        [to_row(_msg("m1", "today we paid the invoice"), names, "c1")],
    )
    reader = FakeReader(chats=[FAMILY])

    substring = _call(reader, store, query="invoice today")
    words = _call(reader, store, query="invoice today", match="words")

    assert substring["hits"] == []
    assert [hit["message"]["id"] for hit in words["hits"]] == ["m1"]
    assert words["filters"]["match"] == "words"


def test_words_combine_with_kinds_and_paging(store: Archive) -> None:
    names = {"u1": "Soda"}
    store.upsert_messages(
        [
            to_row(_msg("m1", "invoice today", ms=1_000), names, "c1"),
            to_row(_msg("m2", "invoice today", ms=2_000), names, "c1"),
            to_row(_msg("m3", "invoice today", ms=3_000, ct=1), names, "c1"),
        ]
    )
    reader = FakeReader(chats=[FAMILY])

    result = _call(
        reader, store, query="invoice today", match="words", kinds=["text"], limit=1, offset=1
    )

    assert [hit["message"]["id"] for hit in result["hits"]] == ["m1"]
    assert result["truncated"] is False


def test_a_words_snippet_centres_on_a_matched_term(store: Archive) -> None:
    long_text = f"{'a' * 80} the invoice is ready {'b' * 80} today"
    store.upsert_messages([to_row(_msg("m1", long_text), {"u1": "Soda"}, "c1")])

    result = _call(FakeReader(chats=[FAMILY]), store, query="invoice today", match="words")

    hit = result["hits"][0]
    assert "invoice" in hit["snippet"]
    assert hit["snippet"].startswith("…")
    assert hit["snippet"].endswith("…")
