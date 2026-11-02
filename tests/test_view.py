"""Pure message rendering: kinds, summaries, views, rows, chat matching."""

from __future__ import annotations

import json

from line_desktop_mcp.view import (
    describe,
    detail,
    iso_time,
    kind_name,
    match_chat,
    row_to_view,
    snippet,
    to_row,
    to_view,
)

NAMES = {"u1": "Soda", "u2": "แม"}


def _base(**extra):
    message = {
        "id": "m1",
        "from": "u1",
        "deliveredTime": 1_700_000_000_000,
        "contentType": 0,
        "text": "hi",
    }
    message.update(extra)
    return message


# -- kinds -------------------------------------------------------------------
def test_kind_name_maps_line_content_types() -> None:
    assert kind_name(0) == "text"
    assert kind_name(1) == "image"
    assert kind_name(7) == "sticker"
    assert kind_name(22) == "flex"


def test_kind_name_keeps_unknown_types_visible() -> None:
    assert kind_name(999) == "type999"
    assert kind_name(None) == "text"


def test_iso_time_is_none_for_missing_values() -> None:
    assert iso_time(0) is None
    assert iso_time(None) is None
    assert iso_time("nope") is None


def test_iso_time_is_local_and_second_precise() -> None:
    rendered = iso_time(1_700_000_000_000)

    assert rendered is not None
    assert rendered[4] == "-" and rendered[-6] in "+-"
    assert len(rendered) >= 19


# -- summaries and details ---------------------------------------------------
def test_text_messages_need_no_summary() -> None:
    assert describe("text", _base(), "hi") is None
    assert describe("text", _base(), None) == "empty message"


def test_sticker_summary_names_the_package() -> None:
    message = _base(contentType=7, text="", contentMetadata={"STKPKGID": "11537"})

    assert describe("sticker", message, None) == "sent a sticker (package 11537)"
    assert detail("sticker", message) == {"sticker": {"package_id": "11537"}}


def test_file_summary_carries_name_and_size() -> None:
    message = _base(
        contentType=14, text="", contentMetadata={"FILE_NAME": "a.pdf", "FILE_SIZE": "1234"}
    )

    assert describe("file", message, None) == "sent a file a.pdf (1234 bytes)"
    assert detail("file", message) == {"file": {"name": "a.pdf", "size": 1234}}


def test_location_summary_and_coordinates() -> None:
    message = _base(
        contentType=15,
        text="",
        location={"title": "Siam", "address": "Rama 1", "latitude": 13.7, "longitude": 100.5},
    )

    assert describe("location", message, None) == "shared a location: Siam"
    assert detail("location", message)["location"]["latitude"] == 13.7


def test_detail_drops_empty_values() -> None:
    assert detail("contact", _base(contentType=13, text="")) == {}


# -- views -------------------------------------------------------------------
def test_to_view_names_the_sender_and_orders_fields() -> None:
    view = to_view(_base(), NAMES)

    assert view["id"] == "m1"
    assert view["from"] == "Soda"
    assert view["kind"] == "text"
    assert view["text"] == "hi"
    assert "summary" not in view


def test_to_view_marks_a_sealed_message_it_cannot_open() -> None:
    view = to_view(_base(text="", chunks=[{"cipher": "..."}]), NAMES)

    assert view["sealed"] is True
    assert view["text"] is None


def test_to_view_decrypts_when_a_decoder_is_given() -> None:
    sealed = _base(text="", chunks=[{"cipher": "..."}])

    def decrypt(message):
        return {**message, "text": "opened"}

    view = to_view(sealed, NAMES, decrypt)

    assert view["text"] == "opened"
    assert "sealed" not in view


def test_to_view_falls_back_to_a_mid_prefix_for_unknown_senders() -> None:
    view = to_view(_base(**{"from": "u999999999999"}), NAMES)

    assert view["from"] == "u999999999"


# -- rows --------------------------------------------------------------------
def test_row_round_trips_through_the_archive_shape() -> None:
    row = to_row(_base(), NAMES, "c1")
    view = row_to_view(row)

    assert row["chat_mid"] == "c1"
    assert row["from_name"] == "Soda"
    assert row["sealed"] == 0
    assert json.loads(str(row["raw_json"]))["text"] == "hi"
    assert view["text"] == "hi"
    assert view["from"] == "Soda"


def test_row_marks_sealed_messages() -> None:
    row = to_row(_base(text="", chunks=[{"cipher": "..."}]), NAMES, "c1")

    assert row["sealed"] == 1
    assert row["text"] is None


def test_row_to_view_survives_broken_raw_json() -> None:
    view = row_to_view(
        {
            "id": "m1",
            "kind": "text",
            "text": "hello",
            "delivered_ms": 1_700_000_000_000,
            "from_name": "Soda",
            "raw_json": "{not json",
        }
    )

    assert view["text"] == "hello"


# -- chat matching -----------------------------------------------------------
CHATS = [
    {"mid": "c1", "name": "Family"},
    {"mid": "c2", "name": "Work Team"},
    {"mid": "u3", "name": "Work Team"},
]


def test_match_chat_prefers_an_exact_mid() -> None:
    chat, candidates = match_chat("c2", CHATS)

    assert chat is not None and chat["mid"] == "c2"
    assert candidates == []


def test_match_chat_reports_ambiguity_instead_of_guessing() -> None:
    chat, candidates = match_chat("work team", CHATS)

    assert chat is None
    assert len(candidates) == 2


def test_match_chat_accepts_a_unique_substring() -> None:
    chat, candidates = match_chat("fam", CHATS)

    assert chat is not None and chat["mid"] == "c1"


def test_a_snippet_keeps_a_short_message_whole() -> None:
    assert snippet("ask for the invoice today", "invoice") == "ask for the invoice today"


def test_a_snippet_adds_ellipses_on_both_sides() -> None:
    text = f"{'a' * 200}invoice{'b' * 200}"

    piece = snippet(text, "invoice", radius=10)

    assert piece == f"…{'a' * 10}invoice{'b' * 10}…"


def test_a_snippet_at_the_start_has_no_leading_ellipsis() -> None:
    piece = snippet(f"invoice {'b' * 200}", "invoice", radius=10)

    assert piece.startswith("invoice")
    assert not piece.startswith("…")
    assert piece.endswith("…")


def test_a_snippet_at_the_end_has_no_trailing_ellipsis() -> None:
    piece = snippet(f"{'a' * 200} invoice", "invoice", radius=10)

    assert piece.endswith("invoice")
    assert not piece.endswith("…")
    assert piece.startswith("…")


def test_a_snippet_ignores_ascii_case() -> None:
    assert snippet("send the INVOICE today", "invoice") == "send the INVOICE today"


def test_a_snippet_works_on_thai_text() -> None:
    piece = snippet("ฝากเร่งดำเนินการเรื่องเปลี่ยนแอร์หน่อยค่ะ", "แอร์", radius=5)

    assert "แอร์" in piece


def test_a_snippet_falls_back_to_the_opening_when_nothing_matches() -> None:
    piece = snippet(f"{'a' * 200} nothing here", "invoice", radius=10)

    assert piece == f"{'a' * 10}…"


def test_match_chat_returns_nothing_for_an_unknown_name() -> None:
    chat, candidates = match_chat("nope", CHATS)

    assert chat is None
    assert candidates == []
