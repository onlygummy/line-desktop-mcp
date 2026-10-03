"""Unit tests for line-desktop-mcp (no browser needed; the client is stubbed).

The shared LineClient lives in line_desktop_mcp.client, so every test swaps
that one name for FakeClient instead of patching each tool module.
"""

import asyncio
import os
import unittest
from typing import Any
from unittest.mock import patch

from line_ext_msg import (
    ChatsViewMissing,
    LineError,
    LoginRequired,
    LoginTimeout,
    Message,
    Room,
    ScanProgress,
    StepResult,
)
from line_ext_msg import (
    __all__ as LINE_EXT_ALL,
)

from line_desktop_mcp import client as shared
from line_desktop_mcp.cli import build_parser
from line_desktop_mcp.tools import line_msg as lm
from line_desktop_mcp.tools.session import _needs_confirm

ALL_TOOLS = (
    "line_status",
    "list_rooms",
    "get_messages",
    "unread_digest",
    "unread_full",
    "search_messages",
    "probe_session",
    "clear_session",
)


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
            name="Work Team",
            unread=0,
            last_preview="ส่งไฟล์แล้ว",
            last_time="9:00 AM",
        ),
    ]


def _messages(count: int = 2) -> "FakeMessages":
    """Fake Messages result: a list of real Message dataclasses."""
    out = FakeMessages(
        Message(
            id=f"m{i}",
            date="2026-10-03",
            ts=f"2026-10-03T08:1{i}:00",
            sender="Mom",
            from_me=False,
            type="text",
            text=f"hello {i}",
        )
        for i in range(count)
    )
    return out


class FakeMessages(list):
    """Stand-in for line_ext_msg.Messages: a list that also carries scroll_stop."""

    scroll_stop = ""


class FakeClient:
    """Minimal LineClient stand-in recording what each tool asked for.

    Not a context manager: client.py owns the lifecycle and yields a plain
    instance, so the stub only needs the methods the tools call.
    """

    def __init__(self, **kwargs: object) -> None:
        self.init_kwargs = kwargs
        FakeClient.instances.append(self)

    # Class-level state, so assertions can reach it without a live client.
    instances: list = []
    calls: dict = {}
    returns: dict = {}
    errors: dict = {}

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.calls = {}
        cls.returns = {}
        cls.errors = {}

    @classmethod
    def called(cls, name: str) -> list:
        return cls.calls.get(name, [])

    @classmethod
    def _record(cls, name: str, kwargs: dict, default: Any) -> Any:
        """Record a call, raise a scripted error, or return the queued result.

        A queued error can be a single exception (raised on every call) or a
        list that shifts on each call, so a retry can succeed after the first
        attempt fails. The default mirrors what the real library hands back
        for a method that found nothing, so a test only has to queue results
        it actually cares about.
        """
        cls.calls.setdefault(name, []).append(kwargs)
        errors = cls.errors.get(name)
        if isinstance(errors, list):
            if errors:
                err = errors.pop(0)
                if err is not None:
                    raise err
        elif errors is not None:
            raise errors
        return cls.returns.get(name, default)

    # -- LineClient surface used by the tools -------------------------

    def status(self, **kwargs):
        return self._record("status", kwargs, [])

    def list_rooms(self, **kwargs):
        return self._record("list_rooms", kwargs, [])

    def get_messages(self, **kwargs):
        return self._record("get_messages", kwargs, FakeMessages())

    def unread_digest(self):
        return self._record("unread_digest", {}, [])

    def unread_full(self, **kwargs):
        return self._record("unread_full", kwargs, [])

    def search_all(self, keyword, **kwargs):
        return self._record("search_all", {"keyword": keyword, **kwargs}, [])

    def probe_session(self):
        return self._record("probe_session", {}, {})

    def logout(self, **kwargs):
        return self._record("logout", kwargs, {})

    def close(self):
        return self._record("close", {}, None)


class StubbedClientCase(unittest.IsolatedAsyncioTestCase):
    """Base case: swap the shared client for the stub and clean up after."""

    def setUp(self) -> None:
        FakeClient.reset()
        shared.reset()
        patcher = patch.object(shared, "LineClient", FakeClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shared.reset)

    async def call(self, tool: str, args: dict | None = None) -> Any:
        """Invoke a tool through the real MCP surface and return its payload.

        Typed as Any because FastMCP resolves the tool's return value at
        runtime, so there is nothing concrete to promise here.
        """
        from fastmcp import Client

        from line_desktop_mcp.server import mcp

        async with Client(mcp) as client:
            result = await client.call_tool(tool, args or {})
        return getattr(result, "data", None)


class ResolveTest(unittest.TestCase):
    def test_by_index(self):
        self.assertEqual(lm._resolve_ref(1, _rooms()).id, "mid-work")

    def test_by_id(self):
        self.assertEqual(lm._resolve_ref("mid-family", _rooms()).index, 0)

    def test_by_name_substring(self):
        self.assertEqual(lm._resolve_ref("ครอบ", _rooms()).id, "mid-family")

    def test_name_match_is_case_insensitive(self):
        """Upstream 3.1 matches names case-insensitively; so must the mirror."""
        self.assertEqual(lm._resolve_ref("work team", _rooms()).id, "mid-work")

    def test_multi_word_name_needs_every_word(self):
        """Reordered words are not a substring, so this exercises the
        contains-all-words pass rather than the plain substring one."""
        self.assertEqual(lm._resolve_ref("team work", _rooms()).id, "mid-work")

    def test_blank_ref_does_not_match_first_room(self):
        """all() over an empty word list is True, so the guard has to hold."""
        from line_ext_msg import RoomNotFound

        for blank in ("", "   "):
            with self.assertRaises(RoomNotFound):
                lm._resolve_ref(blank, _rooms())

    def test_not_found_raises(self):
        from line_ext_msg import RoomNotFound

        with self.assertRaises(RoomNotFound):
            lm._resolve_ref("ไม่มีห้องนี้", _rooms())


class ResolveTargetsTest(unittest.TestCase):
    def test_bad_ref_becomes_entry_not_crash(self):
        targets, skipped = lm._resolve_targets(["Work", "ไม่มีห้องนี้"], _rooms())
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
        self.assertEqual(lm._ok({"a": 1}), {"ok": True, "data": {"a": 1}})

    def test_fail_carries_next_hint(self):
        out = lm._fail(LoginRequired("ยังไม่ล็อกอิน LINE"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "LoginRequired")
        self.assertIn("next", out)

    def test_subclass_reports_its_own_name_and_hint(self):
        """LoginTimeout is a LoginRequired subclass, so exact matching would
        have reported the parent's name and silently dropped the hint."""
        out = lm._fail(LoginTimeout("no scan in time"))
        self.assertEqual(out["error"], "LoginTimeout")
        self.assertIn("next", out)

    def test_unknown_subclass_inherits_parent_hint(self):
        class _FutureSubclass(LoginRequired):
            pass

        out = lm._fail(_FutureSubclass("boom"))
        self.assertEqual(out["error"], "_FutureSubclass")
        self.assertEqual(out["next"], lm._NEXT["LoginRequired"])

    def test_base_class_error_uses_the_generic_hint(self):
        """The MRO ends at LineError, so a hint there means no typed failure
        can ever reach the model without guidance."""
        out = lm._fail(LineError("plain"))
        self.assertEqual(out["error"], "LineError")
        self.assertEqual(out["next"], lm._NEXT["LineError"])


class LoginRequiredHintTest(unittest.TestCase):
    """The hint must not describe a window state that does not exist.

    3.1 raises LoginRequired in four situations, none of which leaves a QR
    window open on screen. Telling the model to look for one sends the user
    hunting for a window that was never shown.
    """

    #: Phrases that assert a window is currently visible.
    FALSE_CLAIMS = ("should be open", "is on the user's screen", "is open on")

    def test_points_at_the_documented_way_to_retry(self):
        hint = lm._NEXT["LoginRequired"]
        self.assertIn("timeout_sec=0", hint)
        self.assertIn("with its defaults", hint)

    def test_does_not_claim_a_window_is_open(self):
        hint = lm._NEXT["LoginRequired"]
        for phrase in self.FALSE_CLAIMS:
            self.assertNotIn(phrase, hint, f"hint wrongly claims {phrase!r}")

    def test_login_timeout_may_claim_the_window(self):
        """The timeout path is the one case where the dialog really is up,
        so that hint is expected to say so."""
        hint = lm._NEXT["LoginTimeout"]
        self.assertTrue(
            any(phrase in hint for phrase in self.FALSE_CLAIMS),
            "LoginTimeout fires with the QR dialog on screen and should say so",
        )

    def test_every_hint_avoids_a_removed_parameter(self):
        """wait_for_login is no longer a tool parameter, so a hint naming it
        would send the model to call a tool argument that does not exist."""
        for name, hint in lm._NEXT.items():
            self.assertNotIn("wait_for_login", hint, f"hint for {name} names a dropped parameter")


class ErrorHintCoverageTest(unittest.TestCase):
    """Every typed error the library exports must reach the model with advice.

    Guards the failure mode where upstream adds a new error class and the MCP
    returns a bare `ok: false` the model cannot act on.
    """

    def test_every_exported_error_has_a_hint(self):
        missing = [
            name
            for name in LINE_EXT_ALL
            if isinstance(getattr(__import__("line_ext_msg"), name), type)
            and issubclass(getattr(__import__("line_ext_msg"), name), LineError)
            and lm._NEXT.get(name) is None
        ]
        self.assertEqual(missing, [], f"no _NEXT hint for: {missing}")


class LineStatusTest(StubbedClientCase):
    """line_status is the setup entry: it waits for the QR by default."""

    def setUp(self) -> None:
        super().setUp()
        FakeClient.returns["status"] = [StepResult(name="Logged in", passed=True, detail="ok")]

    async def test_default_waits_without_naming_the_policy(self):
        """wait_for_login is not passed at all: the library waits by default,
        and timeout_sec=0 is the documented way to ask it not to."""
        await self.call("line_status")
        self.assertEqual(FakeClient.called("status"), [{"login_timeout_ms": 300000}])

    async def test_zero_timeout_skips_the_qr(self):
        await self.call("line_status", {"timeout_sec": 0})
        self.assertEqual(FakeClient.called("status"), [{"login_timeout_ms": 0}])

    async def test_timeout_is_configurable(self):
        await self.call("line_status", {"timeout_sec": 30})
        self.assertEqual(FakeClient.called("status")[0]["login_timeout_ms"], 30000)

    async def test_login_required_envelope_does_not_claim_a_window(self):
        FakeClient.errors["status"] = LoginRequired("not logged in to LINE")
        payload = await self.call("line_status")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "LoginRequired")
        self.assertIn("with its defaults", payload["next"])

    async def test_login_timeout_carries_its_own_hint(self):
        FakeClient.errors["status"] = LoginTimeout("nobody scanned")
        payload = await self.call("line_status")
        self.assertEqual(payload["error"], "LoginTimeout")
        self.assertIn("next", payload)
        self.assertNotEqual(payload["next"], lm._NEXT["LoginRequired"])


class ChatsViewMissingTest(StubbedClientCase):
    async def test_envelope_suggests_dumping_the_dom(self):
        FakeClient.errors["list_rooms"] = ChatsViewMissing("no chat list")
        payload = await self.call("list_rooms")
        self.assertEqual(payload["error"], "ChatsViewMissing")
        self.assertIn("--dump", payload["next"])


class GetMessagesTest(StubbedClientCase):
    async def test_include_media_maps_to_with_media(self):
        FakeClient.returns["get_messages"] = _messages()
        await self.call("get_messages", {"include_media": True})
        sent = FakeClient.called("get_messages")[0]
        self.assertTrue(sent["with_media"])
        self.assertNotIn("include_media_data", sent)

    async def test_time_window_is_passed_through(self):
        FakeClient.returns["get_messages"] = _messages()
        await self.call(
            "get_messages",
            {"date": "2026-10-03", "time_from": "09:00", "time_to": "18:00"},
        )
        sent = FakeClient.called("get_messages")[0]
        self.assertEqual(sent["time_from"], "09:00")
        self.assertEqual(sent["time_to"], "18:00")

    async def test_complete_scan_reports_no_partial(self):
        msgs = _messages()
        msgs.scroll_stop = "top"
        FakeClient.returns["get_messages"] = msgs
        payload = await self.call("get_messages")
        self.assertFalse(payload["data"]["partial"])
        self.assertEqual(payload["data"]["scroll_stop"], "top")
        self.assertNotIn("next", payload["data"])

    async def test_budget_stop_reports_partial_with_hint(self):
        msgs = _messages(1)
        msgs.scroll_stop = "budget"
        FakeClient.returns["get_messages"] = msgs
        payload = await self.call("get_messages")
        self.assertTrue(payload["data"]["partial"])
        self.assertEqual(payload["data"]["messages"][0]["text"], "hello 0")
        self.assertIn("budget", payload["data"]["next"])

    async def test_retries_once_after_wrong_view(self):
        """Resolving `room` calls list_rooms() upstream, which fails when a
        previous call left the page on a room view."""
        FakeClient.returns["get_messages"] = _messages()
        FakeClient.errors["get_messages"] = [ChatsViewMissing("wrong view")]
        payload = await self.call("get_messages", {"room": "work", "limit": 3})
        self.assertTrue(payload["ok"])
        self.assertEqual(len(FakeClient.called("get_messages")), 2)
        self.assertEqual(len(FakeClient.called("status")), 1)


class SearchMessagesTest(StubbedClientCase):
    def setUp(self) -> None:
        super().setUp()
        FakeClient.returns["list_rooms"] = _rooms()

    async def test_resolved_rooms_reach_search_all(self):
        await self.call("search_messages", {"keyword": "invoice", "rooms": ["work"]})
        sent = FakeClient.called("search_all")[0]
        # Room objects, not the raw ref: the library passes those through.
        self.assertEqual([r.id for r in sent["rooms"]], ["mid-work"])

    async def test_bad_ref_is_reported_and_does_not_fail_the_call(self):
        FakeClient.returns["search_all"] = []
        payload = await self.call(
            "search_messages", {"keyword": "invoice", "rooms": ["work", "nope"]}
        )
        errors = [e for e in payload["data"] if e.get("error") == "RoomNotFound"]
        self.assertEqual(len(errors), 1)
        self.assertTrue(payload["ok"])

    async def test_truncated_room_is_kept_and_annotated(self):
        """A partial scan cannot claim a keyword is absent, so the room stays
        in the result even with no messages, and says why."""
        FakeClient.returns["search_all"] = [
            {"room": {"name": "work"}, "messages": [], "truncated": True}
        ]
        payload = await self.call("search_messages", {"keyword": "invoice", "rooms": ["work"]})
        entry = payload["data"][0]
        self.assertTrue(entry["truncated"])
        self.assertIn("next", entry)

    async def test_complete_room_gets_no_hint(self):
        FakeClient.returns["search_all"] = [
            {"room": {"name": "work"}, "messages": [{"text": "invoice"}], "truncated": False}
        ]
        payload = await self.call("search_messages", {"keyword": "invoice", "rooms": ["work"]})
        self.assertNotIn("next", payload["data"][0])

    async def test_progress_callback_is_wired_to_the_tick(self):
        seen: list[tuple[int, int]] = []
        with patch.object(lm, "_notify", side_effect=lambda loop, ctx, p, t: seen.append((p, t))):
            await self.call("search_messages", {"keyword": "invoice", "rooms": ["work"]})
            on_progress = FakeClient.called("search_all")[0]["on_progress"]
            self.assertTrue(callable(on_progress))
            # Driven inside the patch: the adapter resolves _notify from the
            # module globals, so the stub only applies while it is installed.
            on_progress(
                ScanProgress(room=_rooms()[1], index=2, total=5, matched=1, truncated=False)
            )
        self.assertEqual(seen, [(2, 5)])

    async def test_retries_once_after_wrong_view(self):
        """A room view left behind by get_messages makes the first list_rooms
        fail; the tool must navigate back via status() and retry."""
        FakeClient.errors["list_rooms"] = [ChatsViewMissing("wrong view")]
        payload = await self.call("search_messages", {"keyword": "invoice", "rooms": ["work"]})
        self.assertTrue(payload["ok"])
        self.assertEqual(len(FakeClient.called("list_rooms")), 2)
        self.assertEqual(len(FakeClient.called("status")), 1)

    async def test_second_wrong_view_failure_propagates(self):
        """Two failures in a row is a real LINE UI change, not a stale page."""
        FakeClient.errors["list_rooms"] = [
            ChatsViewMissing("wrong view"),
            ChatsViewMissing("wrong view"),
        ]
        payload = await self.call("search_messages", {"keyword": "invoice", "rooms": ["work"]})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "ChatsViewMissing")


class UnreadFullTest(StubbedClientCase):
    async def test_delegates_with_date_and_progress(self):
        FakeClient.returns["unread_full"] = []
        await self.call("unread_full", {"limit_per_room": 5, "date": "2026-10-03"})
        sent = FakeClient.called("unread_full")[0]
        self.assertEqual(sent["limit_per_room"], 5)
        self.assertEqual(sent["date"], "2026-10-03")
        self.assertTrue(callable(sent["on_progress"]))

    async def test_truncated_entry_gets_a_hint(self):
        FakeClient.returns["unread_full"] = [
            {"room": {"unread": 5}, "messages": [], "truncated": True}
        ]
        payload = await self.call("unread_full")
        self.assertIn("next", payload["data"][0])

    async def test_retries_once_after_wrong_view(self):
        FakeClient.errors["unread_full"] = [ChatsViewMissing("wrong view")]
        payload = await self.call("unread_full")
        self.assertTrue(payload["ok"])
        self.assertEqual(len(FakeClient.called("unread_full")), 2)
        self.assertEqual(len(FakeClient.called("status")), 1)


class SharedClientTest(StubbedClientCase):
    """One client per server, dropped when the page behind it dies."""

    async def test_client_is_reused_across_tool_calls(self):
        FakeClient.returns["list_rooms"] = _rooms()
        await self.call("list_rooms")
        await self.call("list_rooms")
        self.assertEqual(len(FakeClient.instances), 1)
        self.assertEqual(len(FakeClient.called("list_rooms")), 2)

    async def test_failure_drops_the_client_so_the_next_call_rebuilds(self):
        FakeClient.returns["list_rooms"] = _rooms()
        FakeClient.errors["list_rooms"] = LoginRequired("no session")
        payload = await self.call("list_rooms")
        self.assertFalse(payload["ok"])

        del FakeClient.errors["list_rooms"]
        await self.call("list_rooms")
        self.assertEqual(len(FakeClient.instances), 2)


class SessionTest(StubbedClientCase):
    def test_needs_confirm_shape(self):
        out = _needs_confirm()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "NeedsConfirmation")
        self.assertIn("next", out)

    async def test_refuses_without_confirm(self):
        payload = await self.call("clear_session")
        self.assertEqual(payload["error"], "NeedsConfirmation")
        self.assertEqual(FakeClient.called("logout"), [])

    async def test_confirmed_call_reaches_logout(self):
        """clear_session was renamed upstream; the deprecated alias still
        resolves, but only logout() may be called so the warning never fires."""
        FakeClient.returns["logout"] = {"wiped": True}
        payload = await self.call("clear_session", {"confirm": True})
        self.assertTrue(payload["ok"])
        self.assertEqual(FakeClient.called("logout"), [{"backup": True}])

    async def test_logout_primes_the_page_fail_fast_first(self):
        """logout() reaches for a ready page, which with no session would open
        the QR window and wait. The prime must therefore pass wait_for_login
        =False, and must survive that prime raising."""
        FakeClient.errors["status"] = LoginRequired("no session")
        FakeClient.returns["logout"] = {"wiped": True}
        payload = await self.call("clear_session", {"confirm": True, "backup": False})
        self.assertTrue(payload["ok"])
        self.assertEqual(FakeClient.called("status"), [{"wait_for_login": False}])
        self.assertEqual(FakeClient.called("logout"), [{"backup": False}])

    async def test_probe_session_goes_through_the_shared_client(self):
        FakeClient.returns["probe_session"] = {"logged_in": True}
        payload = await self.call("probe_session")
        self.assertTrue(payload["data"]["logged_in"])
        self.assertEqual(len(FakeClient.instances), 1)


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


class ToolTimeoutTest(unittest.TestCase):
    """No tool carries a timeout.

    FastMCP wraps a sync tool in anyio.fail_after, but the worker thread is
    not preempted (abandon_on_cancel=False), so a timeout waits for the
    blocking call to finish and then discards its result. A filtered scan
    now runs up to a minute per room and the login wait up to five minutes,
    so a timeout would only ever discard work, never cut it short. The
    library's own scroll budget bounds scans and reports `truncated`, and
    `line_status` reports `LoginTimeout`.
    """

    def _timeout(self, name: str):
        from line_desktop_mcp.server import mcp

        tool = asyncio.run(mcp.get_tool(name))
        assert tool is not None
        return tool.timeout

    def test_no_tool_has_a_timeout(self):
        for name in ALL_TOOLS:
            self.assertIsNone(self._timeout(name), name)


class ToolSurfaceTest(unittest.IsolatedAsyncioTestCase):
    async def _params(self) -> dict[str, set[str]]:
        """Parameter names per tool, read from the advertised JSON schema."""
        from fastmcp import Client

        from line_desktop_mcp.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
        return {tool.name: set(tool.input_schema.get("properties", {})) for tool in tools}

    async def test_all_expected_tools_exist(self):
        self.assertEqual(set(await self._params()), set(ALL_TOOLS))

    async def test_batch_tools_no_longer_offer_scroll(self):
        """scroll=False on a filtered scan reads only on-screen rows, which
        reports "no match" for a room the scan never reached. The upstream
        methods do not accept it either, so the parameter is gone."""
        params = await self._params()
        for name in ("unread_full", "search_messages"):
            self.assertNotIn("scroll", params[name], name)

    async def test_get_messages_offers_the_time_window(self):
        params = await self._params()
        self.assertIn("time_from", params["get_messages"])
        self.assertIn("time_to", params["get_messages"])

    async def test_line_status_offers_only_a_timeout(self):
        """wait_for_login was dropped: a model reaching for it would send an
        argument the schema rejects, and timeout_sec=0 covers that case."""
        params = await self._params()
        self.assertIn("timeout_sec", params["line_status"])
        self.assertNotIn("wait_for_login", params["line_status"])


class CliTest(unittest.TestCase):
    def test_defaults(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.transport, "stdio")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8000)


if __name__ == "__main__":
    unittest.main()
