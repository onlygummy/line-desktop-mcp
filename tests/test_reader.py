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


class DirectoryApi(FakeApi):
    """FakeApi plus the calls `LineReader.directory` makes."""

    def __init__(self, *, boxes, chats, contacts):
        super().__init__()
        self._boxes = boxes
        self._chats = chats
        self._contacts = contacts

    def get_message_boxes(self, **_kwargs):
        return {"messageBoxes": self._boxes}

    def get_chats(self, mids, with_members=False):
        return {"chats": [chat for chat in self._chats if chat["chatMid"] in mids]}

    def get_all_contact_ids(self):
        return list(self._contacts)

    def get_contacts(self, mids):
        return {
            "contacts": {
                mid: {"contact": {"displayName": self._contacts[mid]}}
                for mid in mids
                if mid in self._contacts
            }
        }


def _box(mid: str, *, sender: str = "", unread: int = 0) -> dict:
    return {
        "id": mid,
        "unreadCount": unread,
        "lastMessages": [{"from": sender or mid, "deliveredTime": 5_000, "text": "hi"}],
    }


def test_a_direct_chat_is_named_after_the_peer(session_file: Path) -> None:
    api = DirectoryApi(
        boxes=[_box("u2", sender="u2", unread=1)],
        chats=[{"chatMid": "u2", "type": 2}],
        contacts={"u2": "Mint"},
    )

    (chat,) = _reader(session_file, api=api).directory()

    assert chat["name"] == "Mint"
    assert chat["kind"] == "dm"
    assert chat["unread"] == 1


def test_a_room_is_named_after_a_member_when_the_last_message_is_ours(
    session_file: Path,
) -> None:
    """A room has no `chatName`, and the sender fallback cannot help when we wrote last."""
    api = DirectoryApi(
        boxes=[_box("r1", sender="u1")],
        chats=[{"chatMid": "r1", "type": 1, "memberMids": ["u1", "u3"]}],
        contacts={"u3": "Ploy"},
    )

    (chat,) = _reader(session_file, api=api).directory()

    assert chat["name"] == "Ploy"
    assert chat["kind"] == "room"


def test_a_named_group_keeps_its_line_name(session_file: Path) -> None:
    api = DirectoryApi(
        boxes=[_box("c1", sender="u2")],
        chats=[
            {
                "chatMid": "c1",
                "chatName": "หอพักหมวกแดงSCC",
                "type": 0,
                "extra": {"groupExtra": {"memberMids": ["u1", "u2"]}},
            }
        ],
        contacts={"u2": "Mint"},
    )

    (chat,) = _reader(session_file, api=api).directory()

    assert chat["name"] == "หอพักหมวกแดงSCC"


def test_an_unresolvable_chat_keeps_the_mid_stub(session_file: Path) -> None:
    api = DirectoryApi(
        boxes=[_box("u9abcdef")],
        chats=[{"chatMid": "u9abcdef", "type": 2}],
        contacts={},
    )

    (chat,) = _reader(session_file, api=api).directory()

    assert chat["name"] == "Chat u9abcd"


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
