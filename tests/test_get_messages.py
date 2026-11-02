"""`get_messages`: hybrid fetch, archiving, image backup, resolution errors."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from line_desktop_mcp.archive import Archive
from line_desktop_mcp.tools import get_messages as tool
from line_desktop_mcp.view import to_row
from stubs import FakeReader, StubMCP

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
FAMILY = {"mid": "c1", "name": "Family", "kind": "group", "unread": 1, "last_ms": 3_000}


def msg(message_id: str, text: str = "hi", ms: int = 1_000, ct: int = 0, **extra):
    message = {
        "id": message_id,
        "from": "u1",
        "deliveredTime": ms,
        "contentType": ct,
        "text": text,
    }
    message.update(extra)
    return message


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Archive:
    monkeypatch.setenv("LINE_DESKTOP_MCP_MEDIA", str(tmp_path / "media"))
    archive = Archive(tmp_path / "archive.db")
    yield archive
    archive.close()


def _call(reader: FakeReader, archive: Archive, chat: str = "Family", limit: int = 30, **extra):
    mcp = StubMCP()
    tool.register(mcp, reader, archive)  # type: ignore[arg-type]
    return mcp.tools["get_messages"](chat=chat, limit=limit, **extra)


def _payload(blocks) -> dict:
    return json.loads(blocks[0].text)


def _at(year: int, month: int, day: int, hour: int = 12) -> int:
    return int(datetime(year, month, day, hour).timestamp() * 1000)


def test_returns_messages_oldest_first(store: Archive) -> None:
    reader = FakeReader(chats=[FAMILY], recent=[msg("m2", ms=2_000), msg("m1", ms=1_000)])

    payload = _payload(_call(reader, store, limit=10))

    assert [message["id"] for message in payload["messages"]] == ["m1", "m2"]
    assert payload["source"] == "api"
    assert payload["partial"] is False


def test_preflight_runs_before_anything_else(store: Archive) -> None:
    reader = FakeReader(chats=[FAMILY], recent=[msg("m1")])

    _call(reader, store)

    assert reader.calls[0] == "ensure_ready"


def test_every_message_read_is_archived(store: Archive) -> None:
    reader = FakeReader(chats=[FAMILY], recent=[msg("m1"), msg("m2", ms=2_000)])

    _call(reader, store, limit=10)

    assert [row["id"] for row in store.messages("c1", limit=10)] == ["m2", "m1"]


def test_older_messages_come_from_the_archive_first(store: Archive) -> None:
    store.upsert_messages([to_row(msg("m0", ms=500), {"u1": "Soda"}, "c1")])
    reader = FakeReader(chats=[FAMILY], recent=[msg("m1", ms=1_000)])

    payload = _payload(_call(reader, store, limit=2))

    assert [message["id"] for message in payload["messages"]] == ["m0", "m1"]
    assert payload["source"] == "mixed"
    assert ("previous", "m1") not in reader.calls


def test_pages_backwards_when_the_archive_is_short(store: Archive) -> None:
    reader = FakeReader(
        chats=[FAMILY],
        recent=[msg("m1", ms=1_000)],
        previous={"m1": [msg("m0", ms=500)]},
    )

    payload = _payload(_call(reader, store, limit=2))

    assert [message["id"] for message in payload["messages"]] == ["m0", "m1"]
    assert ("previous", "m1") in reader.calls


def test_sender_names_are_resolved_before_rendering(store: Archive) -> None:
    reader = FakeReader(
        chats=[FAMILY],
        recent=[msg("m1", ms=1_000)],
        names={"u1": "Soda"},
    )

    payload = _payload(_call(reader, store, limit=5))

    assert payload["messages"][0]["from"] == "Soda"
    assert ("resolve_names", 1) in reader.calls


def test_a_full_page_from_line_is_not_labelled_mixed(store: Archive) -> None:
    store.upsert_messages([to_row(msg("m0", ms=500), {"u1": "Soda"}, "c1")])
    reader = FakeReader(chats=[FAMILY], recent=[msg("m2", ms=2_000), msg("m1", ms=1_000)])

    payload = _payload(_call(reader, store, limit=2))

    assert [message["id"] for message in payload["messages"]] == ["m1", "m2"]
    assert payload["source"] == "api"


def test_a_short_chat_is_not_partial(store: Archive) -> None:
    reader = FakeReader(chats=[FAMILY], recent=[msg("m1")])

    payload = _payload(_call(reader, store, limit=5))

    assert payload["returned"] == 1
    assert payload["partial"] is False


def test_an_ambiguous_chat_lists_the_candidates(store: Archive) -> None:
    reader = FakeReader(
        chats=[FAMILY, {"mid": "c2", "name": "Family", "kind": "group", "unread": 0}]
    )

    with pytest.raises(ToolError, match="2 chats match"):
        _call(reader, store, chat="Family")


def test_an_unknown_chat_asks_for_the_listing(store: Archive) -> None:
    reader = FakeReader(chats=[FAMILY])

    with pytest.raises(ToolError, match="No chat matches"):
        _call(reader, store, chat="Nowhere")


def test_serves_the_archive_when_line_is_unreachable(store: Archive) -> None:
    store.upsert_messages([to_row(msg("m1"), {"u1": "Soda"}, "c1")])
    reader = FakeReader(chats=[FAMILY], recent=ToolError("LINE request failed: offline"))

    payload = _payload(_call(reader, store, limit=5))

    assert payload["stale"] is True
    assert payload["source"] == "db"
    assert [message["id"] for message in payload["messages"]] == ["m1"]


def test_images_are_saved_and_attached(store: Archive) -> None:
    image = msg("m1", text="", ct=1, contentMetadata={"FILE_SIZE": "9"})
    reader = FakeReader(chats=[FAMILY], recent=[image], download=lambda _message: PNG)

    blocks = _call(reader, store, limit=10)

    payload = _payload(blocks)
    saved = Path(payload["images"][0]["path"])

    assert len(blocks) == 2  # JSON plus one image
    assert blocks[1].__class__.__name__ == "Image"
    assert saved.exists()
    assert store.media_for(["m1"])["m1"]["ext"] == "png"


def test_a_failed_download_is_reported_not_fatal(store: Archive) -> None:
    image = msg("m1", text="", ct=1)
    reader = FakeReader(chats=[FAMILY], recent=[image], download=lambda _message: None)

    blocks = _call(reader, store, limit=10)

    payload = _payload(blocks)

    assert payload["images"] == []
    assert payload["images_failed"] == ["m1"]
    assert len(blocks) == 1


def test_an_already_archived_image_is_served_from_disk(store: Archive) -> None:
    image = msg("m1", text="", ct=1)
    reader = FakeReader(chats=[FAMILY], recent=[image], download=lambda _message: PNG)
    _call(reader, store, limit=10)

    reader.downloaded.clear()
    blocks = _call(reader, store, limit=10)

    assert reader.downloaded == []  # nothing fetched twice
    assert len(blocks) == 2


def test_non_image_media_stays_text_only(store: Archive) -> None:
    document = msg("m1", text="", ct=14, contentMetadata={"FILE_NAME": "a.pdf"})
    reader = FakeReader(chats=[FAMILY], recent=[document], download=lambda _message: b"%PDF")

    blocks = _call(reader, store, limit=10)

    payload = _payload(blocks)

    assert len(blocks) == 1
    assert payload["messages"][0]["kind"] == "file"
    assert payload["images"] == []


def test_a_window_the_archive_covers_never_asks_line(store: Archive) -> None:
    names = {"u1": "Soda"}
    store.upsert_messages(
        [
            to_row(msg("m1", ms=_at(2026, 10, 5)), names, "c1"),
            to_row(msg("m2", ms=_at(2026, 10, 6)), names, "c1"),
        ]
    )
    reader = FakeReader(chats=[FAMILY], recent=[msg("m9", ms=_at(2026, 11, 1))])

    payload = _payload(_call(reader, store, limit=2, after="2026-10-01", before="2026-10-31"))

    assert payload["source"] == "db"
    assert [message["id"] for message in payload["messages"]] == ["m1", "m2"]
    assert ("recent", "c1", 50) not in reader.calls
    assert payload["window"] == {"after": "2026-10-01", "before": "2026-10-31"}


def test_a_window_fills_the_rest_from_line(store: Archive) -> None:
    store.upsert_messages([to_row(msg("m1", ms=_at(2026, 10, 5)), {"u1": "Soda"}, "c1")])
    reader = FakeReader(
        chats=[FAMILY],
        recent=[msg("m2", ms=_at(2026, 10, 6)), msg("m3", ms=_at(2026, 10, 7))],
    )

    payload = _payload(_call(reader, store, limit=3, after="2026-10-01", before="2026-10-31"))

    assert payload["source"] == "mixed"
    assert [message["id"] for message in payload["messages"]] == ["m1", "m2", "m3"]


def test_a_window_stops_at_its_older_edge(store: Archive) -> None:
    reader = FakeReader(chats=[FAMILY], recent=[msg("m1", ms=_at(2026, 9, 1))])

    payload = _payload(_call(reader, store, limit=5, after="2026-10-01"))

    assert payload["returned"] == 0
    assert payload["partial"] is False
    assert payload["stale"] is False


def test_a_window_reports_partial_when_the_budget_runs_out(store: Archive) -> None:
    stamps = {f"m{i}": _at(2026, 10, i) for i in range(5, 11)}
    reader = FakeReader(
        chats=[FAMILY],
        recent=[msg("m10", ms=stamps["m10"])],
        previous={f"m{i}": [msg(f"m{i - 1}", ms=stamps[f"m{i - 1}"])] for i in range(6, 11)},
    )

    payload = _payload(_call(reader, store, limit=10, after="2026-10-01", before="2026-11-01"))

    assert payload["returned"] == 6
    assert payload["partial"] is True


def test_an_empty_window_is_rejected(store: Archive) -> None:
    reader = FakeReader(chats=[FAMILY], recent=[msg("m1")])

    with pytest.raises(ToolError, match="earlier than"):
        _call(reader, store, after="2026-11-30", before="2026-11-01")


def test_a_window_works_offline(store: Archive) -> None:
    store.upsert_messages([to_row(msg("m1", ms=_at(2026, 10, 5)), {"u1": "Soda"}, "c1")])
    reader = FakeReader(chats=[FAMILY], recent=ToolError("LINE request failed: offline"))

    payload = _payload(_call(reader, store, limit=5, after="2026-10-01"))

    assert payload["stale"] is True
    assert payload["source"] == "db"
    assert [message["id"] for message in payload["messages"]] == ["m1"]
