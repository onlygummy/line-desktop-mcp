"""Parse the date bounds the read tools share.

`search_messages` and `get_messages` accept the same `after` / `before`
strings, so the parsing and its error message live here instead of in both.
A bare date means local midnight; `before` covers that whole day.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastmcp.exceptions import ToolError

DATE_FORMAT = "%Y-%m-%d"


def window(after: str | None, before: str | None) -> tuple[int | None, int | None]:
    """Both bounds in epoch milliseconds; an empty window is refused."""
    after_ms = parse_after(after) if after else None
    before_ms = parse_before(before) if before else None
    check(after_ms, before_ms)
    return after_ms, before_ms


def check(after_ms: int | None, before_ms: int | None, *, field: str = "after") -> None:
    """Refuse a window that contains nothing.

    `field` names the input that set the lower bound, so the error points at
    `since_days` when that is where the bound came from.
    """
    if after_ms is not None and before_ms is not None and after_ms >= before_ms:
        raise ToolError(f"`{field}` must be earlier than `before`.")


def parse_after(value: str) -> int:
    """A local date or ISO timestamp to epoch milliseconds."""
    return _moment(value, "after")


def parse_before(value: str) -> int:
    """The exclusive upper bound; a bare date covers that whole local day."""
    raw = value.strip()
    try:
        day = datetime.strptime(raw, DATE_FORMAT)
    except ValueError:
        return _moment(raw, "before")
    return int((day + timedelta(days=1)).timestamp() * 1000)


def _moment(value: str, field: str) -> int:
    raw = value.strip()
    try:
        return int(datetime.strptime(raw, DATE_FORMAT).timestamp() * 1000)
    except ValueError:
        pass
    try:
        return int(datetime.fromisoformat(raw).timestamp() * 1000)
    except ValueError:
        raise ToolError(f"`{field}` must be a date like 2026-11-01, or an ISO timestamp.") from None
