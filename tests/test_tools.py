"""Unit tests for line-desktop-mcp (no browser needed; LineClient is mocked)."""

import asyncio
import os
import unittest
from unittest.mock import patch

from line_ext_msg import LoginRequired, Room, StepResult

from line_desktop_mcp.cli import build_parser
from line_desktop_mcp.tools import line_msg as lm
from line_desktop_mcp.tools.session import _needs_confirm


def _rooms() -> list[Room]:
    """Two rooms for resolve tests (mirrors the upstream Room schema)."""
    return [
        Room(
            index=0,
            id="mid-family",
            name="ครอบครัว",
            unread=3,
            last_preview="กินข้าวยัง",
            last_time="8:13 AM",
        ),
        Room(
            index=1,
            id="mid-work",
            name="งาน",
            unread=0,
            last_preview="ส่งไฟล์แล้ว",
            last_time="9:00 AM",
        ),
    ]


class FakeClient:
    """Minimal LineClient stand-in (context manager + scripted status)."""

    seen_calls: list = []
    messages_calls: list = []
    _steps: list = []
    _error: Exception | None = None
    _error_on_call: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def status(self, **kwargs):
        FakeClient.seen_calls.append(kwargs)
        err = FakeClient._error_on_call.get(len(FakeClient.seen_calls))
        if err is not None:
            raise err
        if FakeClient._error is not None:
            raise FakeClient._error
        return FakeClient._steps

    def get_messages(self, **kwargs):
        FakeClient.messages_calls.append(kwargs)
        return []


class ResolveTest(unittest.TestCase):
    def test_by_index(self):
        self.assertEqual(lm._resolve_ref(1, _rooms()).id, "mid-work")

    def test_by_id(self):
        self.assertEqual(lm._resolve_ref("mid-family", _rooms()).index, 0)

    def test_by_name_substring(self):
        self.assertEqual(lm._resolve_ref("ครอบ", _rooms()).id, "mid-family")

    def test_not_found_raises(self):
        from line_ext_msg import RoomNotFound

        with self.assertRaises(RoomNotFound):
            lm._resolve_ref("ไม่มีห้องนี้", _rooms())


class ResolveTargetsTest(unittest.TestCase):
    def test_bad_ref_becomes_entry_not_crash(self):
        targets, skipped = lm._resolve_targets(["งาน", "ไม่มีห้องนี้"], _rooms())
        self.assertEqual([r.id for r in targets], ["mid-work"])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["error"], "RoomNotFound")
        self.assertIn("next", skipped[0])

    def test_all_good_no_skipped(self):
        targets, skipped = lm._resolve_targets([0, "mid-family"], _rooms())
        self.assertEqual(len(targets), 2)
        self.assertEqual(skipped, [])


class EnvelopeTest(unittest.TestCase):
    def test_ok_shape(self):
        out = lm._ok({"a": 1})
        self.assertEqual(out, {"ok": True, "data": {"a": 1}})

    def test_fail_carries_next_hint(self):
        out = lm._fail(LoginRequired("ยังไม่ล็อกอิน LINE"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "LoginRequired")
        self.assertIn("next", out)


class LineStatusTest(unittest.IsolatedAsyncioTestCase):
    """line_status is the single setup entry: it waits for login by default."""

    def setUp(self):
        FakeClient._steps = [StepResult(name="Logged in", passed=True, detail="ok")]
        FakeClient._error = None
        FakeClient._error_on_call = {}
        FakeClient.seen_calls = []

    async def _call(self, args: dict):
        from fastmcp import Client

        from line_desktop_mcp.server import mcp

        with patch.object(lm, "LineClient", FakeClient):
            async with Client(mcp) as client:
                result = await client.call_tool("line_status", args)
        return getattr(result, "data", None)

    async def test_waits_for_login_by_default(self):
        await self._call({})
        self.assertEqual(
            FakeClient.seen_calls,
            [{"wait_for_login": True, "login_timeout_ms": 180000}],
        )

    async def test_fail_fast_when_disabled(self):
        await self._call({"wait_for_login": False})
        self.assertEqual(FakeClient.seen_calls, [{}])

    async def test_login_required_becomes_envelope(self):
        FakeClient._error = LoginRequired("not logged in to LINE")
        payload = await self._call({})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "LoginRequired")
        self.assertIn("next", payload)


class LibraryEnvTest(unittest.TestCase):
    """_configure_library must set this MCP's own env defaults."""

    KEYS = ("LINE_EXT_MSG_PROFILE", "LINE_EXT_MSG_PORT", "LINE_EXT_MSG_DIALOG_TITLE")

    def setUp(self):
        self._saved = {key: os.environ.get(key) for key in self.KEYS}

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_defaults(self):
        from line_desktop_mcp import server

        for key in self.KEYS:
            os.environ.pop(key, None)
        server._configure_library()
        self.assertTrue(os.environ["LINE_EXT_MSG_PROFILE"].endswith("line-desktop-mcp"))
        self.assertEqual(os.environ["LINE_EXT_MSG_PORT"], "9223")
        self.assertEqual(os.environ["LINE_EXT_MSG_DIALOG_TITLE"], "Line Desktop MCP")

    def test_existing_values_are_preserved(self):
        from line_desktop_mcp import server

        os.environ["LINE_EXT_MSG_PROFILE"] = r"C:\custom\profile"
        os.environ["LINE_EXT_MSG_PORT"] = "9999"
        os.environ["LINE_EXT_MSG_DIALOG_TITLE"] = "Custom Title"
        server._configure_library()
        self.assertEqual(os.environ["LINE_EXT_MSG_PROFILE"], r"C:\custom\profile")
        self.assertEqual(os.environ["LINE_EXT_MSG_PORT"], "9999")
        self.assertEqual(os.environ["LINE_EXT_MSG_DIALOG_TITLE"], "Custom Title")


class GetMessagesMediaTest(unittest.IsolatedAsyncioTestCase):
    """include_media must reach upstream as the 2.0 `with_media` kwarg."""

    def setUp(self):
        FakeClient.messages_calls = []

    async def test_include_media_maps_to_with_media(self):
        from fastmcp import Client

        from line_desktop_mcp.server import mcp

        with patch.object(lm, "LineClient", FakeClient):
            async with Client(mcp) as client:
                await client.call_tool("get_messages", {"include_media": True})
        self.assertEqual(len(FakeClient.messages_calls), 1)
        self.assertTrue(FakeClient.messages_calls[0]["with_media"])
        self.assertNotIn("include_media_data", FakeClient.messages_calls[0])


class SessionGateTest(unittest.TestCase):
    def test_needs_confirm_shape(self):
        out = _needs_confirm()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "NeedsConfirmation")
        self.assertIn("next", out)


class ToolTimeoutTest(unittest.TestCase):
    """Lifecycle tools must run without a tool-level timeout.

    FastMCP wraps a sync tool in anyio.fail_after, but the worker thread is
    not preempted (abandon_on_cancel=False), so a timeout waits for the
    blocking call to finish and then discards its result. Long setup flows
    (install, QR login) must therefore stay untimed.
    """

    def _timeout(self, name: str):
        from line_desktop_mcp.server import mcp

        tool = asyncio.run(mcp.get_tool(name))
        assert tool is not None
        return tool.timeout

    def test_lifecycle_tools_have_no_timeout(self):
        for name in (
            "line_status",
            "list_rooms",
            "get_messages",
            "unread_digest",
            "probe_session",
            "clear_session",
        ):
            self.assertIsNone(self._timeout(name), name)

    def test_batch_tools_keep_a_timeout(self):
        for name in ("unread_full", "search_messages"):
            self.assertEqual(self._timeout(name), 300.0, name)


class ToolSurfaceTest(unittest.IsolatedAsyncioTestCase):
    """wait_login was folded into line_status(wait_for_login=...)."""

    async def test_wait_login_tool_is_gone(self):
        from fastmcp import Client

        from line_desktop_mcp.server import mcp

        async with Client(mcp) as client:
            names = {tool.name for tool in await client.list_tools()}
        self.assertNotIn("wait_login", names)
        self.assertIn("line_status", names)


class CliTest(unittest.TestCase):
    def test_defaults(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.transport, "stdio")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8000)


if __name__ == "__main__":
    unittest.main()
