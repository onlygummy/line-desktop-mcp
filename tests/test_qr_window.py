"""The window's pure parts: no Tk, no display, no window needed."""

from __future__ import annotations

from okline.qrterm import qr_matrix

from line_desktop_mcp.qr_window import (
    TARGET_QR_PX,
    card_size,
    dark_cells,
    format_pin,
    scale_for,
)


def test_dark_cells_matches_the_matrix() -> None:
    matrix = qr_matrix("https://line.me/qr/test")

    expected = [
        (column, row)
        for row, cells in enumerate(matrix)
        for column, is_dark in enumerate(cells)
        if is_dark
    ]

    assert list(dark_cells(matrix)) == expected
    assert expected  # a QR code always has dark modules


def test_dark_cells_skips_light_modules() -> None:
    assert list(dark_cells([[False, False], [False, False]])) == []
    assert list(dark_cells([[True, False], [False, True]])) == [(0, 0), (1, 1)]


def test_format_pin_spaces_every_digit() -> None:
    assert format_pin("123456") == "1 2 3 4 5 6"
    assert format_pin(" 42 ") == "4 2"


def test_format_pin_leaves_short_values_alone() -> None:
    assert format_pin("") == ""
    assert format_pin("7") == "7"


def test_card_size_keeps_the_floor() -> None:
    assert card_size(None, (100, 80)) == (200, 120)


def test_card_size_grows_for_a_wide_line_of_text() -> None:
    # 460 of text minus the 22px padding on both sides.
    assert card_size(None, (460, 90)) == (416, 120)


def test_card_size_never_clips_the_qr() -> None:
    assert card_size((320, 320), (100, 80)) == (320, 320)


def test_scale_for_fits_the_reserved_box() -> None:
    for modules in (21, 29, 33, 45, 57):
        assert modules * scale_for(modules) <= TARGET_QR_PX


def test_scale_for_never_returns_a_hairline() -> None:
    assert scale_for(0) == 2
    assert scale_for(1000) == 2
