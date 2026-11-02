"""`list_chats`: filtering, archive persistence, offline fallback."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from line_desktop_mcp.archive import Archive
from line_desktop_mcp.tools import list_chats as tool
from stubs import FakeReader, StubMCP

CHATS = [
    {
        "mid": "c1",
        "name": "Family",
        "kind": "group",
        "unread": 3,
        "last_ms": 3_000,
        "last_preview": "hi",
    },
    {
        "mid": "c2",
        "name": "Work Team",
        "kind": "group",
        "unread": 0,
        "last_ms": 2_000,
        "last_preview": None,
    },
]


@pytest.fixture()
def archive(tmp_path: Path) -> Archive:
    store = Archive(tmp_path / "archive.db")
    yield store
    store.close()


def _call(reader: FakeReader, archive: Archive, **kwargs):
    mcp = StubMCP()
    tool.register(mcp, reader, archive)  # type: ignore[arg-type]
    return mcp.tools["list_chats"](**kwargs)


def test_lists_chats_with_name_kind_and_unread(archive: Archive) -> None:
    result = _call(FakeReader(chats=CHATS), archive)

    assert result["ok"] is True
    assert [chat["mid"] for chat in result["chats"]] == ["c1", "c2"]
    assert result["unread_total"] == 3
    assert result["stale"] is False


def test_filters_unread_only(archive: Archive) -> None:
    result = _call(FakeReader(chats=CHATS), archive, unread_only=True)

    assert [chat["mid"] for chat in result["chats"]] == ["c1"]


def test_filters_by_name(archive: Archive) -> None:
    result = _call(FakeReader(chats=CHATS), archive, query="work")

    assert [chat["mid"] for chat in result["chats"]] == ["c2"]


def test_respects_the_limit(archive: Archive) -> None:
    result = _call(FakeReader(chats=CHATS), archive, limit=1)

    assert result["returned"] == 1


def test_archives_what_it_listed(archive: Archive) -> None:
    _call(FakeReader(chats=CHATS), archive)

    assert [chat["mid"] for chat in archive.chats()] == ["c1", "c2"]


def test_falls_back_to_the_archive_when_line_fails(archive: Archive) -> None:
    archive.upsert_chats(CHATS)
    reader = FakeReader(chats=ToolError("LINE request failed: offline"))

    result = _call(reader, archive)

    assert result["stale"] is True
    assert [chat["mid"] for chat in result["chats"]] == ["c1", "c2"]


def test_raises_when_everything_fails(archive: Archive) -> None:
    reader = FakeReader(chats=ToolError("LINE request failed: offline"))

    with pytest.raises(ToolError, match="offline"):
        _call(reader, archive)


def test_refreshes_once_when_the_query_misses_the_cache(archive: Archive) -> None:
    reader = FakeReader(chats=CHATS)

    _call(reader, archive, query="nothing-matches")

    assert ("directory", True) in reader.calls
