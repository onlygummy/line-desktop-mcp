"""`search_messages`: archive hits, scoping, and the opt-in LINE harvest."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from line_desktop_mcp.archive import Archive
from line_desktop_mcp.tools import search_messages as tool
from line_desktop_mcp.view import to_row
from stubs import FakeReader, StubMCP

FAMILY = {"mid": "c1", "name": "Family", "kind": "group", "unread": 0}
WORK = {"mid": "c2", "name": "Work Team", "kind": "group", "unread": 0}


def _msg(message_id: str, text: str, ms: int = 1_000) -> dict:
    return {
        "id": message_id,
        "from": "u1",
        "deliveredTime": ms,
        "contentType": 0,
        "text": text,
    }


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
    assert result["coverage"]["messages_searched"] == 1
    assert result["coverage"]["chats_total"] == 1


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
    with pytest.raises(ToolError, match="must not be empty"):
        _call(FakeReader(chats=[FAMILY]), store, query="   ")


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
    assert result["api_pages"] == 1
    assert [hit["message"]["id"] for hit in result["hits"]] == ["m2", "m1", "m0"]


def test_sync_still_searches_when_line_has_nothing_more(store: Archive) -> None:
    store.upsert_messages([to_row(_msg("m1", "invoice"), {"u1": "Soda"}, "c1")])
    reader = FakeReader(chats=[FAMILY], previous={})

    result = _call(reader, store, query="invoice", chats=["Family"], sync=True)

    assert result["api_pages"] == 0
    assert result["returned"] == 1
