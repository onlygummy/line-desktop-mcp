"""Login flow tests: no network, no Node, no Tk -- every boundary is faked."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from okline.exceptions import LineError
from okline.hmac_signer import HmacSignerError

from line_desktop_mcp.tools import login


class FakeE2ee:
    def __init__(self, ready: bool) -> None:
        self._ready = ready

    def is_ready(self) -> bool:
        return self._ready


class FakeLoginResult:
    def __init__(self, success: bool = True, display_message: str | None = None) -> None:
        self.success = success
        self.display_message = display_message
        self.type = 1


class Fake:
    """Per-test knobs shared by the fake client (reset by the fixture)."""

    profile: dict = {"mid": "u1", "displayName": "Soda"}
    raises: Exception | None = None
    stale_session: bool = False
    gate: threading.Event | None = None
    instances: list = []


class FakeOkLine:
    def __init__(self) -> None:
        self.saved: list[str] = []
        self.closed = False
        self.wait_seconds: float | None = None
        self.e2ee = FakeE2ee(True)
        Fake.instances.append(self)

    @classmethod
    def from_tokens_file(cls, path: str, **kw: object) -> FakeOkLine:
        if Fake.stale_session:
            raise LineError("session expired")
        return cls()

    def get_profile(self) -> dict:
        if Fake.raises is not None and not Fake.stale_session:
            raise Fake.raises
        return dict(Fake.profile)

    def qr_login(self, *, on_qr, on_pin=None, wait_seconds: float = 180.0) -> FakeLoginResult:
        self.wait_seconds = wait_seconds
        on_qr("https://line.me/qr/test")
        if Fake.raises is not None:
            raise Fake.raises
        if Fake.gate is not None:
            Fake.gate.wait(5)
        return FakeLoginResult()

    def save_tokens(self, path: str) -> None:
        self.saved.append(path)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> FakeOkLine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FakeWindow:
    usable = True
    cancel_on_start = False
    instances: list[FakeWindow] = []

    def __init__(self, title: str = "Line Desktop MCP") -> None:
        self.title = title
        self.error = None if FakeWindow.usable else "no display"
        self.cancelled = threading.Event()
        self.qr_urls: list[str] = []
        self.pins: list[str] = []
        self.last_url: str | None = None
        self.started = False
        self.closed = False
        self.finished: str | None = None
        self.failed: str | None = None
        FakeWindow.instances.append(self)

    def start(self) -> None:
        self.started = True
        if FakeWindow.cancel_on_start:
            self.cancelled.set()

    def show_qr(self, url: str) -> None:
        self.last_url = url
        self.qr_urls.append(url)

    def show_pin(self, pin: str) -> None:
        self.pins.append(pin)

    def set_status(self, text: str) -> None:
        pass

    def finish(self, message: str) -> None:
        self.finished = message

    def fail(self, message: str = "") -> None:
        self.failed = message

    def close(self) -> None:
        self.closed = True

    def join(self, timeout: float | None = None) -> None:
        return None


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the flow at fakes and at a throwaway session file."""
    session_file = tmp_path / "session.json"
    monkeypatch.setattr(login, "OkLine", FakeOkLine)
    monkeypatch.setattr(login, "QRWindow", FakeWindow)
    monkeypatch.setenv(login.SESSION_ENV, str(session_file))
    Fake.profile = {"mid": "u1", "displayName": "Soda"}
    Fake.raises = None
    Fake.stale_session = False
    Fake.gate = None
    Fake.instances = []
    FakeWindow.instances = []
    FakeWindow.usable = True
    FakeWindow.cancel_on_start = False
    return session_file


def test_fresh_login_saves_session_and_returns_profile(fakes: Path) -> None:
    result = login.run_login(30)

    assert result["ok"] is True
    assert result["already_logged_in"] is False
    assert result["mid"] == "u1"
    assert result["profile"]["display_name"] == "Soda"
    assert result["e2ee_ready"] is True
    assert result["ui"] == "window"
    assert result["session_path"] == str(fakes)

    window = FakeWindow.instances[0]
    assert window.started is True
    assert window.qr_urls == ["https://line.me/qr/test"]
    assert window.finished == "Logged in as Soda"

    client = Fake.instances[0]
    assert client.saved == [str(fakes)]
    assert client.wait_seconds == 30
    assert client.closed is True


def test_existing_session_short_circuits_without_a_window(fakes: Path) -> None:
    fakes.write_text("{}", encoding="utf-8")

    result = login.run_login(30)

    assert result["already_logged_in"] is True
    assert result["ui"] == "none"
    assert result["mid"] == "u1"
    assert FakeWindow.instances == []


def test_stale_session_falls_through_to_a_fresh_login(fakes: Path) -> None:
    fakes.write_text("{}", encoding="utf-8")
    Fake.stale_session = True

    result = login.run_login(30)

    assert result["already_logged_in"] is False
    assert result["ui"] == "window"


def test_force_ignores_a_working_session(fakes: Path) -> None:
    fakes.write_text("{}", encoding="utf-8")

    result = login.run_login(30, force=True)

    assert result["already_logged_in"] is False
    assert result["ui"] == "window"


def test_cancel_returns_an_error_and_saves_nothing() -> None:
    FakeWindow.cancel_on_start = True
    Fake.gate = threading.Event()

    with pytest.raises(ToolError, match="cancelled"):
        login.run_login(30)

    assert Fake.instances[0].saved == []
    assert FakeWindow.instances[0].failed is None
    Fake.gate.set()


def test_timeout_reports_how_to_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(login, "_GRACE_SEC", 0.3)
    Fake.gate = threading.Event()
    try:
        with pytest.raises(ToolError, match="No QR scan"):
            login.run_login(0.1)
    finally:
        Fake.gate.set()

    assert FakeWindow.instances[0].failed is not None


def test_missing_node_gets_an_install_hint() -> None:
    Fake.raises = HmacSignerError("Node.js not found")

    with pytest.raises(ToolError, match="Node.js 18"):
        login.run_login(30)


def test_rejected_login_surfaces_the_line_message() -> None:
    Fake.raises = LineError("code 10201: invalid request")

    with pytest.raises(ToolError, match="LINE login failed"):
        login.run_login(30)


def test_second_login_is_refused_while_one_runs() -> None:
    login._LOGIN_LOCK.acquire()
    try:
        with pytest.raises(ToolError, match="already running"):
            login.run_login(30)
    finally:
        login._LOGIN_LOCK.release()


def test_login_never_writes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    login.run_login(30)

    assert capsys.readouterr().out == ""


def test_window_failure_falls_back_to_a_text_qr(fakes: Path) -> None:
    FakeWindow.usable = False

    result = login.run_login(30)

    assert result["ui"] == "text"
    assert (fakes.parent / "qr.txt").exists()
