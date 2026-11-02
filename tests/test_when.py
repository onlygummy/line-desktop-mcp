"""`when.window`: the shared date bounds for the read tools."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastmcp.exceptions import ToolError

from line_desktop_mcp import when


def _at(year: int, month: int, day: int, hour: int = 0) -> int:
    return int(datetime(year, month, day, hour).timestamp() * 1000)


def test_a_date_means_local_midnight() -> None:
    assert when.parse_after("2026-11-01") == _at(2026, 11, 1)


def test_before_covers_the_whole_day() -> None:
    assert when.parse_before("2026-11-01") == _at(2026, 11, 2)


def test_an_iso_timestamp_is_kept_to_the_second() -> None:
    assert when.parse_after("2026-11-01T10:30:00") == _at(2026, 11, 1, 10) + 30 * 60_000


def test_a_bad_value_names_the_field() -> None:
    with pytest.raises(ToolError, match="`after` must be a date"):
        when.parse_after("01/11/2026")
    with pytest.raises(ToolError, match="`before` must be a date"):
        when.parse_before("tomorrow")


def test_an_empty_window_is_refused() -> None:
    with pytest.raises(ToolError, match="earlier than"):
        when.window("2026-11-30", "2026-11-01")


def test_missing_bounds_stay_none() -> None:
    assert when.window(None, None) == (None, None)


def test_a_single_day_window_is_allowed() -> None:
    after, before = when.window("2026-11-01", "2026-11-01")

    assert after == _at(2026, 11, 1)
    assert before == _at(2026, 11, 2)


def test_check_names_the_field_that_set_the_bound() -> None:
    with pytest.raises(ToolError, match="`since_days` must be earlier"):
        when.check(2_000, 1_000, field="since_days")
