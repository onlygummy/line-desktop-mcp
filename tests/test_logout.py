"""`logout`: revoke the session, delete the credentials, keep the archive."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from line_desktop_mcp.tools import logout as tool
from line_desktop_mcp.tools.login import SESSION_LOCK
from stubs import FakeReader, StubMCP


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LINE_DESKTOP_MCP_SESSION", str(tmp_path / "session.json"))
    monkeypatch.setenv("LINE_DESKTOP_MCP_DB", str(tmp_path / "archive.db"))
    monkeypatch.setenv("LINE_DESKTOP_MCP_MEDIA", str(tmp_path / "media"))
    return tmp_path


def _session(home: Path) -> Path:
    path = home / "session.json"
    path.write_text("{}", encoding="utf-8")
    return path


def _call(reader: FakeReader) -> dict:
    mcp = StubMCP()
    tool.register(mcp, reader)  # type: ignore[arg-type]
    return mcp.tools["logout"]()


def test_logout_revokes_and_deletes_the_session(home: Path) -> None:
    path = _session(home)
    (home / "archive.db").write_bytes(b"db")
    (home / "media").mkdir()
    (home / "media" / "m1.jpg").write_bytes(b"img")
    (home / "qr.txt").write_text("qr", encoding="utf-8")
    reader = FakeReader()

    result = _call(reader)

    assert result["ok"] is True
    assert result["already_logged_out"] is False
    assert result["session_deleted"] is True
    assert result["remote"] == "revoked"
    assert result["archive"] == "kept"
    assert not path.exists()
    assert not (home / "qr.txt").exists()
    assert reader.calls == ["revoke", "shutdown"]
    # Data is not a credential: the archive and the images stay.
    assert (home / "archive.db").read_bytes() == b"db"
    assert (home / "media" / "m1.jpg").exists()


def test_logout_without_a_session_does_nothing(home: Path) -> None:
    reader = FakeReader()

    result = _call(reader)

    assert result["already_logged_out"] is True
    assert result["session_deleted"] is False
    assert result["remote"] == "skipped"
    assert reader.calls == []


def test_a_failed_revoke_still_deletes_the_local_session(home: Path) -> None:
    path = _session(home)
    reader = FakeReader(revoke_result={"remote": "failed", "error": "offline"})

    result = _call(reader)

    assert result["remote"] == "failed"
    assert result["remote_error"] == "offline"
    assert result["session_deleted"] is True
    assert not path.exists()


def test_an_unexpected_revoke_error_still_deletes_the_session(home: Path) -> None:
    """The local file is the risk: no failure above may leave it behind."""
    path = _session(home)
    reader = FakeReader(revoke_error=RuntimeError("boom"))

    result = _call(reader)

    assert result["remote"] == "failed"
    assert "boom" in result["remote_error"]
    assert not path.exists()
    assert reader.calls == ["revoke", "shutdown"]


def test_logout_refuses_while_a_login_runs(home: Path) -> None:
    path = _session(home)
    assert SESSION_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ToolError, match="login is already running"):
            _call(FakeReader())
    finally:
        SESSION_LOCK.release()

    assert path.exists()


def test_a_second_logout_is_a_no_op(home: Path) -> None:
    _session(home)
    _call(FakeReader())

    result = _call(FakeReader())

    assert result["already_logged_out"] is True
    assert result["session_deleted"] is False


def test_a_locked_session_file_is_reported(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _session(home)
    monkeypatch.setattr(tool, "_remove_quietly", lambda _path: None)

    with pytest.raises(ToolError, match="could not be deleted"):
        _call(FakeReader())


def test_logout_never_writes_to_stdout(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _session(home)

    _call(FakeReader())

    assert capsys.readouterr().out == ""


def test_logout_is_marked_destructive() -> None:
    mcp = StubMCP()
    tool.register(mcp, FakeReader())  # type: ignore[arg-type]

    annotations = mcp.annotations["logout"]

    assert annotations.read_only_hint is False
    assert annotations.destructive_hint is True
    assert annotations.idempotent_hint is True
