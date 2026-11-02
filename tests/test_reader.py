"""The LINE reader's preconditions and error mapping (no network, no Node)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from okline.exceptions import LineTransportError

from line_desktop_mcp import reader as reader_module
from line_desktop_mcp.reader import LineReader


class FakeE2ee:
    def __init__(self, ready: bool = True) -> None:
        self._ready = ready

    def is_ready(self) -> bool:
        return self._ready


class FakeApi:
    def __init__(self, *, e2ee_ready: bool = True, fail_profile: Exception | None = None) -> None:
        self.e2ee = FakeE2ee(e2ee_ready)
        self.closed = False
        self._fail_profile = fail_profile
        self.profile = {"mid": "u1", "displayName": "Soda"}

    def close(self) -> None:
        self.closed = True

    def get_profile(self):
        if self._fail_profile is not None:
            raise self._fail_profile
        return dict(self.profile)


@pytest.fixture()
def session_file(tmp_path: Path) -> Path:
    path = tmp_path / "session.json"
    path.write_text("{}", encoding="utf-8")
    return path


def _reader(session: Path, api: FakeApi | None = None, calls: list | None = None) -> LineReader:
    made = api or FakeApi()
    log = calls if calls is not None else []

    def factory(path: str) -> FakeApi:
        log.append(path)
        return made

    return LineReader(session_path=lambda: session, api_factory=factory)  # type: ignore[arg-type]


def test_missing_session_asks_for_login(tmp_path: Path) -> None:
    line = _reader(tmp_path / "absent.json")

    with pytest.raises(ToolError, match="No LINE session yet"):
        line.ensure_ready()


def test_missing_node_is_reported_before_anything_else(
    session_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reader_module.LtsmBridge, "is_available", staticmethod(lambda: False))
    line = _reader(session_file)

    with pytest.raises(ToolError, match="Node.js is required"):
        line.ensure_ready()


def test_a_broken_session_file_is_reported(session_file: Path) -> None:
    def factory(path: str):
        raise ValueError("bad json")

    line = LineReader(session_path=lambda: session_file, api_factory=factory)  # type: ignore[arg-type]

    with pytest.raises(ToolError, match="could not be opened"):
        line.ensure_ready()


def test_the_client_is_built_once_per_file_version(session_file: Path) -> None:
    calls: list[str] = []
    line = _reader(session_file, calls=calls)

    first = line.ensure_ready()
    second = line.ensure_ready()

    assert first == second
    assert len(calls) == 1


def test_a_new_session_file_rebuilds_the_client(session_file: Path) -> None:
    calls: list[str] = []
    line = _reader(session_file, calls=calls)
    line.ensure_ready()

    os.utime(session_file, (1, 1))
    line.ensure_ready()

    assert len(calls) == 2


def test_a_rejected_session_becomes_an_actionable_error(session_file: Path) -> None:
    api = FakeApi(fail_profile=LineTransportError("HTTP 401"))
    line = _reader(session_file, api=api)
    line.ensure_ready()

    with pytest.raises(ToolError, match="Call login again"):
        line.profile()

    assert api.closed is True
    assert line._ready is None


def test_ready_reports_e2ee_state(session_file: Path) -> None:
    line = _reader(session_file, api=FakeApi(e2ee_ready=False))

    ready = line.ensure_ready()

    assert ready.e2ee_ready is False
    assert ready.node_available is True
    assert ready.session_path == session_file


def test_download_returns_none_when_obs_fails(session_file: Path) -> None:
    api = FakeApi()

    def boom(*args, **kwargs):
        raise LineTransportError("obs down")

    api.obs = type("Obs", (), {"download_object": staticmethod(boom)})()  # type: ignore[attr-defined]
    line = _reader(session_file, api=api)
    line.ensure_ready()

    assert line.download({"id": "m1", "contentMetadata": {}}) is None


def test_calls_are_serialised(session_file: Path) -> None:
    """Two tool calls must not share one requests.Session mid-flight."""
    line = _reader(session_file)
    line.ensure_ready()
    active = 0
    peak = 0
    guard = threading.Lock()

    def work(_api):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return "ok"

    threads = [threading.Thread(target=lambda: line._call(work)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert peak == 1
