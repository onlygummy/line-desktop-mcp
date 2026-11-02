"""`unread`: the digest of unread chats, its caps, and its offline path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from line_desktop_mcp.archive import Archive
from line_desktop_mcp.tools import unread as tool
from line_desktop_mcp.view import to_row
from stubs import FakeReader, StubMCP

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8

FAMILY = {"mid": "c1", "name": "Family", "kind": "group", "unread": 3, "last_ms": 3_000}
WORK = {"mid": "c2", "name": "Work", "kind": "group", "unread": 2, "last_ms": 5_000}
QUIET = {"mid": "c3", "name": "Old", "kind": "dm", "unread": 0, "last_ms": 1_000}


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


def _call(reader: FakeReader, archive: Archive, **kwargs):
    mcp = StubMCP()
    tool.register(mcp, reader, archive)  # type: ignore[arg-type]
    return mcp.tools["unread"](**kwargs)


def _payload(blocks) -> dict:
    return json.loads(blocks[0].text)


def test_only_unread_chats_newest_activity_first(store: Archive) -> None:
    reader = FakeReader(
        chats=[FAMILY, WORK, QUIET],
        recent_by_chat={"c1": [msg("m1")], "c2": [msg("m2")]},
    )

    payload = _payload(_call(reader, store))

    assert [chat["chat"]["mid"] for chat in payload["chats"]] == ["c2", "c1"]
    assert payload["chats_returned"] == 2
    assert payload["chats_skipped"] == 0
    assert payload["unread_total"] == 5


def test_the_cap_reports_what_it_skipped(store: Archive) -> None:
    reader = FakeReader(
        chats=[FAMILY, WORK],
        recent_by_chat={"c1": [msg("m1")], "c2": [msg("m2")]},
    )

    payload = _payload(_call(reader, store, chats_limit=1))

    assert payload["chats_returned"] == 1
    assert payload["chats_skipped"] == 1
    assert payload["unread_total"] == 5  # every unread chat, not just the shown one
    assert payload["chats"][0]["chat"]["mid"] == "c2"


def test_every_message_read_is_archived(store: Archive) -> None:
    reader = FakeReader(
        chats=[FAMILY],
        recent_by_chat={"c1": [msg("m1", ms=1_000), msg("m2", ms=2_000)]},
    )

    _call(reader, store)

    assert [row["id"] for row in store.messages("c1", limit=10)] == ["m2", "m1"]


def test_messages_are_oldest_first_and_capped_per_chat(store: Archive) -> None:
    reader = FakeReader(
        chats=[FAMILY],
        recent_by_chat={"c1": [msg(f"m{i}", ms=i * 1_000) for i in range(5)]},
    )

    payload = _payload(_call(reader, store, per_chat=2))

    assert [message["id"] for message in payload["chats"][0]["messages"]] == ["m3", "m4"]
    assert payload["chats"][0]["returned"] == 2


def test_one_failing_chat_does_not_drop_the_others(store: Archive) -> None:
    store.upsert_messages([to_row(msg("old", ms=500), {"u1": "Soda"}, "c1")])
    reader = FakeReader(
        chats=[FAMILY, WORK],
        recent_by_chat={"c2": [msg("m2", ms=2_000)]},
        fail_recent_for={"c1"},
    )

    payload = _payload(_call(reader, store))

    assert payload["stale"] is False
    assert payload["source"] == "mixed"
    assert payload["chats"][0]["chat"]["mid"] == "c2"
    assert payload["chats"][0]["stale"] is False
    assert payload["chats"][1]["chat"]["mid"] == "c1"
    assert payload["chats"][1]["stale"] is True
    assert [message["id"] for message in payload["chats"][1]["messages"]] == ["old"]


def test_everything_offline_is_served_from_the_archive(store: Archive) -> None:
    store.upsert_chats([FAMILY])
    store.upsert_messages([to_row(msg("m1"), {"u1": "Soda"}, "c1")])
    reader = FakeReader(chats=ToolError("LINE request failed: offline"), fail_recent_for={"c1"})

    payload = _payload(_call(reader, store))

    assert payload["stale"] is True
    assert payload["source"] == "db"
    assert payload["chats"][0]["chat"]["name"] == "Family"
    assert [message["id"] for message in payload["chats"][0]["messages"]] == ["m1"]


def test_a_message_without_a_sender_is_not_looked_up(store: Archive) -> None:
    """A missing `from` must not become the literal mid "None" in a contact call."""
    reader = FakeReader(
        chats=[FAMILY],
        recent_by_chat={
            "c1": [
                msg("m1"),
                {"id": "m2", "deliveredTime": 2_000, "contentType": 0, "text": "system"},
            ]
        },
    )

    _call(reader, store)

    assert ("resolve_names", 1) in reader.calls


def test_the_preflight_runs_first(store: Archive) -> None:
    reader = FakeReader(chats=[FAMILY], recent_by_chat={"c1": [msg("m1")]})

    _call(reader, store)

    assert reader.calls[0] == "ensure_ready"


def test_images_are_not_fetched_by_default(store: Archive) -> None:
    image = msg("m1", text="", ct=1)
    reader = FakeReader(chats=[FAMILY], recent_by_chat={"c1": [image]}, download=lambda _m: PNG)

    blocks = _call(reader, store)

    assert len(blocks) == 1  # the JSON block only
    assert reader.downloaded == []
    assert _payload(blocks)["chats"][0]["images"] == []


def test_images_can_be_included(store: Archive) -> None:
    image = msg("m1", text="", ct=1, contentMetadata={"FILE_SIZE": "9"})
    reader = FakeReader(chats=[FAMILY], recent_by_chat={"c1": [image]}, download=lambda _m: PNG)

    blocks = _call(reader, store, include_images=True)

    payload = _payload(blocks)
    saved = Path(payload["chats"][0]["images"][0]["path"])

    assert len(blocks) == 2  # JSON plus one image
    assert blocks[1].__class__.__name__ == "Image"
    assert saved.exists()
    assert store.media_for(["m1"])["m1"]["ext"] == "png"


def test_annotations_and_silence(store: Archive, capsys: pytest.CaptureFixture[str]) -> None:
    reader = FakeReader(chats=[FAMILY], recent_by_chat={"c1": [msg("m1")]})
    mcp = StubMCP()
    tool.register(mcp, reader, store)  # type: ignore[arg-type]

    mcp.tools["unread"]()

    assert capsys.readouterr().out == ""
    annotations = mcp.annotations["unread"]
    assert annotations.read_only_hint is False
    assert annotations.destructive_hint is False
    assert annotations.idempotent_hint is True
