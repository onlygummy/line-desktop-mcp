"""`history.harvest`: paging backwards and archiving what comes back."""

from __future__ import annotations

from pathlib import Path

import pytest

from line_desktop_mcp.archive import Archive
from line_desktop_mcp.history import harvest
from line_desktop_mcp.view import to_row
from stubs import FakeReader


def _msg(message_id: str, ms: int, text: str = "hi") -> dict:
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


def test_harvest_seeds_from_the_newest_page_when_empty(store: Archive) -> None:
    reader = FakeReader(recent=[_msg("m2", 2_000), _msg("m1", 1_000)], previous={})

    result = harvest(reader, store, "c1")

    assert result["messages"] == 2
    assert [row["id"] for row in store.messages("c1", limit=10)] == ["m2", "m1"]


def test_harvest_pages_back_from_the_oldest_known_message(store: Archive) -> None:
    store.upsert_messages([to_row(_msg("m2", 2_000), {"u1": "Soda"}, "c1")])
    reader = FakeReader(recent=[], previous={"m2": [_msg("m1", 1_000)]})

    result = harvest(reader, store, "c1")

    assert result["pages"] == 1
    assert [row["id"] for row in store.messages("c1", limit=10)] == ["m2", "m1"]
    assert reader.calls[0] == ("previous", "m2")


def test_harvest_stops_on_an_empty_page(store: Archive) -> None:
    store.upsert_messages([to_row(_msg("m9", 9_000), {"u1": "Soda"}, "c1")])
    reader = FakeReader(previous={})

    result = harvest(reader, store, "c1", pages=3)

    assert result == {"pages": 0, "messages": 0}


def test_harvest_stops_when_a_page_adds_nothing_new(store: Archive) -> None:
    store.upsert_messages([to_row(_msg("m9", 9_000), {"u1": "Soda"}, "c1")])
    reader = FakeReader(previous={"m9": [_msg("m9", 9_000)]})

    result = harvest(reader, store, "c1", pages=3)

    assert result == {"pages": 1, "messages": 0}


def test_harvest_resolves_sender_names_before_archiving(store: Archive) -> None:
    reader = FakeReader(recent=[_msg("m1", 1_000)], previous={}, names={"u1": "Soda"})

    harvest(reader, store, "c1")

    (row,) = store.messages("c1", limit=10)
    assert row["from_name"] == "Soda"
