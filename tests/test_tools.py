"""Unit tests for line-desktop-mcp (no browser needed; LineClient is mocked)."""

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


class WaitLoginTest(unittest.TestCase):
    def setUp(self):
        FakeClient._steps = [StepResult(name="ล็อกอินแล้ว", passed=True, detail="ok")]
        FakeClient._error = None
        FakeClient._error_on_call = {}
        FakeClient.seen_calls = []

    def test_success_headless_proves_reuse_without_settle(self):
        with (
            patch.object(lm, "LineClient", FakeClient),
            patch.object(lm, "_debug_headed", return_value=False),
            patch.object(lm, "time") as mock_time,
        ):
            out = lm._wait_login(180)
        self.assertTrue(out["ok"])
        self.assertTrue(out["data"]["logged_in"])
        # Upstream waits inside the first call; the second is a fail-fast
        # headless re-verify. Already headless, so no settle sleep.
        self.assertEqual(
            FakeClient.seen_calls,
            [{"wait_for_login": True, "login_timeout_ms": 180000}, {}],
        )
        mock_time.sleep.assert_not_called()

    def test_headed_login_settles_before_headless_verify(self):
        with (
            patch.object(lm, "LineClient", FakeClient),
            patch.object(lm, "_debug_headed", return_value=True),
            patch.object(lm, "time") as mock_time,
        ):
            out = lm._wait_login(180)
        self.assertTrue(out["ok"])
        # Grace comes before the verify that restarts headed as headless.
        mock_time.sleep.assert_called_once_with(lm._HEADLESS_SETTLE_SEC)
        self.assertEqual(len(FakeClient.seen_calls), 2)

    def test_verify_miss_returns_login_required(self):
        # Login succeeded, but the headless re-verify misses (flush too
        # slow or logged out since): report login required, not success.
        FakeClient._error_on_call = {2: LoginRequired("ยังไม่ล็อกอิน LINE")}
        with (
            patch.object(lm, "LineClient", FakeClient),
            patch.object(lm, "_debug_headed", return_value=False),
            patch.object(lm, "time"),
        ):
            out = lm._wait_login(10)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "LoginRequired")
        self.assertIn("next", out)

    def test_timeout_becomes_login_required_envelope(self):
        FakeClient._error = LoginRequired("ยังไม่ล็อกอิน LINE")
        with (
            patch.object(lm, "LineClient", FakeClient),
            patch.object(lm, "_debug_headed", return_value=False),
            patch.object(lm, "time"),
        ):
            out = lm._wait_login(10)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "LoginRequired")
        self.assertIn("next", out)


class DebugHeadedTest(unittest.TestCase):
    def test_headless_browser_is_not_headed(self):
        with patch("line_ext_msg.chrome.is_headless", return_value=True):
            self.assertFalse(lm._debug_headed())

    def test_headed_browser_is_headed(self):
        with patch("line_ext_msg.chrome.is_headless", return_value=False):
            self.assertTrue(lm._debug_headed())

    def test_probe_error_maps_to_headed(self):
        with patch(
            "line_ext_msg.chrome.is_headless", side_effect=RuntimeError("no cdp")
        ):
            self.assertTrue(lm._debug_headed())


class SessionGateTest(unittest.TestCase):
    def test_needs_confirm_shape(self):
        out = _needs_confirm()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "NeedsConfirmation")
        self.assertIn("next", out)


class CliTest(unittest.TestCase):
    def test_defaults(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.transport, "stdio")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8000)


if __name__ == "__main__":
    unittest.main()
