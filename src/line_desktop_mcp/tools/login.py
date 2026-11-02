"""The login tool, plus the one flow it drives.

Kept in a single module on purpose: the flow exists only to serve this tool.
Once a second concern needs the same session handling (reading chats, probing
state), extract it into its own module -- do not let this file turn into a
general-purpose dumping ground.
"""

from __future__ import annotations

import contextlib
import threading
import time
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from okline import OkLine
from okline.exceptions import LineError
from okline.hmac_signer import HmacSignerError
from okline.qrterm import qr_to_ascii
from pydantic import Field

from line_desktop_mcp.paths import session_path
from line_desktop_mcp.qr_window import QRWindow

#: How long past `timeout_sec` to keep waiting before giving up. The library
#: stops its own long-poll at `timeout_sec`; this covers network slack.
_GRACE_SEC = 30.0

#: How often the tool thread checks whether the window was closed.
_POLL_SEC = 0.2

#: Only one login at a time: two windows would fight over the same file.
_LOGIN_LOCK = threading.Lock()

# Writing a session file is a side effect, but it destroys nothing and the call
# is safe to repeat, because an existing session short-circuits before any
# window opens.
LOGIN_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def _profile_summary(profile: Any) -> dict[str, Any]:
    """Keep the two fields a caller needs; the raw Thrift profile is huge."""
    if not isinstance(profile, dict):
        return {"mid": None, "display_name": None}
    return {
        "mid": profile.get("mid"),
        "display_name": profile.get("displayName"),
    }


def existing_login(path: Path) -> dict[str, Any] | None:
    """Return profile and E2EE state when the saved session still works.

    A stale or corrupt file is not an error: it means "log in again", and the
    caller falls through to the QR flow. Any real problem (missing Node, no
    network) resurfaces there with a message the user can act on.
    """
    if not path.exists():
        return None
    try:
        with OkLine.from_tokens_file(str(path)) as api:
            return {
                "profile": _profile_summary(api.get_profile()),
                "e2ee_ready": bool(api.e2ee.is_ready()),
            }
    except Exception:  # noqa: BLE001 - any failure means "not usable yet"
        return None


def _write_ascii_qr(path: Path, url: str) -> None:
    """Fallback when no window can open: leave the code next to the session."""
    target = path.parent / "qr.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(qr_to_ascii(url), encoding="utf-8")


def _clear_text_qr(path: Path) -> None:
    """Drop the fallback QR once the login is over: it is single-use and secret."""
    with contextlib.suppress(OSError):
        (path.parent / "qr.txt").unlink(missing_ok=True)


def _login_worker(
    path: Path,
    timeout_sec: float,
    window: QRWindow | None,
    abandon: threading.Event,
    box: dict[str, Any],
    done: threading.Event,
) -> None:
    """Run the blocking QR flow off the tool thread, so closing stays responsive."""

    def on_qr(url: str) -> None:
        if window is not None and window.error is None:
            window.show_qr(url)
        else:
            _write_ascii_qr(path, url)

    def on_pin(pin: str) -> None:
        if window is not None:
            window.show_pin(pin)

    api = OkLine()
    try:
        result = api.qr_login(on_qr=on_qr, on_pin=on_pin, wait_seconds=timeout_sec)
        if not result.success:
            box["error"] = (
                "LINE rejected the login "
                f"({result.display_message or result.type}); start over and scan again."
            )
            return
        if abandon.is_set():
            # The user cancelled while the phone was approving: never leave a
            # session behind that they did not ask to keep.
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        api.save_tokens(str(path))
        box["profile"] = _profile_summary(api.get_profile())
        box["e2ee_ready"] = bool(api.e2ee.is_ready())
        _clear_text_qr(path)
    except Exception as exc:  # noqa: BLE001 - mapped to ToolError by the caller
        box["exception"] = exc
    finally:
        api.close()
        done.set()


def _describe_failure(exc: Exception) -> str:
    """Turn a library failure into instructions the user can follow."""
    if isinstance(exc, HmacSignerError):
        return (
            f"{exc} Install Node.js 18 or newer (https://nodejs.org), or point "
            "LINE_NODE at an existing node executable, then retry."
        )
    if isinstance(exc, LineError):
        return (
            f"LINE login failed: {exc}. Check the network and retry. If it keeps "
            "failing, delete the session file and log in from scratch."
        )
    return f"Login failed unexpectedly ({type(exc).__name__}): {exc}"


def _wait_for_scan(
    window: QRWindow | None,
    done: threading.Event,
    timeout_sec: float,
    abandon: threading.Event,
) -> tuple[str, str] | None:
    """Block until the worker finishes; return (state, message) or None."""
    deadline = time.monotonic() + timeout_sec + _GRACE_SEC
    while not done.wait(_POLL_SEC):
        if window is not None and window.error is not None:
            # The child gave up on Tk after start() returned; stop waiting.
            abandon.set()
            return ("failed", window.error)
        if window is not None and window.cancelled.is_set():
            abandon.set()
            return (
                "cancelled",
                "Login cancelled by the user. Nothing was saved; call login "
                "again when they are ready to scan.",
            )
        if time.monotonic() > deadline:
            abandon.set()
            return (
                "failed",
                f"No QR scan within {timeout_sec:g}s. Call login again and have "
                "the user scan promptly; raise timeout_sec if they need longer.",
            )
    return None


def _qr_login(path: Path, timeout_sec: float) -> dict[str, Any]:
    """Open the QR window and wait for the phone, with a working close control."""
    created = QRWindow()
    abandon = threading.Event()
    box: dict[str, Any] = {}
    done = threading.Event()
    # Start the Node/network work first, so it overlaps the window coming up
    # instead of queueing behind it. The QR callback only posts to a queue, so
    # it is safe for it to fire before the window exists.
    worker = threading.Thread(
        target=_login_worker,
        args=(path, timeout_sec, created, abandon, box, done),
        name="line-login",
        daemon=True,
    )
    worker.start()
    created.start()
    # No display (or no Tk): fall back to a text QR the user can open.
    window: QRWindow | None = None if created.error else created
    if window is None and created.last_url:
        # The code may have arrived before Tk gave up; keep it recoverable.
        _write_ascii_qr(path, created.last_url)

    outcome = _wait_for_scan(window, done, timeout_sec, abandon)
    state = "failed"
    failure: str | None = None
    if outcome is not None:
        state, failure = outcome
    elif "exception" in box:
        failure = _describe_failure(box["exception"])
    elif "error" in box:
        failure = box["error"]

    if failure is not None:
        if window is not None:
            if state == "cancelled":
                window.close()
            else:
                # Keep the line short: the window has one fixed-width row and
                # the full reason is already in the tool error.
                window.fail("See the tool result for the reason")
        _clear_text_qr(path)
        raise ToolError(failure)

    profile = box.get("profile") or {"mid": None, "display_name": None}
    if window is not None:
        label = profile.get("display_name") or profile.get("mid") or "your account"
        window.finish(f"Logged in as {label}")
        window.join(3.0)

    return {
        "ok": True,
        "already_logged_in": False,
        "profile": profile,
        "mid": profile.get("mid"),
        "e2ee_ready": bool(box.get("e2ee_ready", False)),
        "session_path": str(path),
        "ui": "window" if window is not None else "text",
    }


def run_login(timeout_sec: float, *, force: bool = False) -> dict[str, Any]:
    """Return the stored session when it works, else run a fresh QR login."""
    path = session_path()

    if not force:
        current = existing_login(path)
        if current is not None:
            return {
                "ok": True,
                "already_logged_in": True,
                "profile": current["profile"],
                "mid": current["profile"].get("mid"),
                "e2ee_ready": current["e2ee_ready"],
                "session_path": str(path),
                "ui": "none",
            }

    if not _LOGIN_LOCK.acquire(blocking=False):
        raise ToolError(
            "A login is already running in another tool call. Wait for it to finish "
            "instead of opening a second QR window."
        )
    try:
        return _qr_login(path, timeout_sec)
    finally:
        _LOGIN_LOCK.release()


def register(mcp: FastMCP) -> None:
    """Register `login`, the tool that makes every other tool possible."""

    @mcp.tool(
        name="login",
        title="Log in to LINE",
        tags={"login"},
        annotations=LOGIN_ANNOTATIONS,
    )
    def login(
        timeout_sec: Annotated[
            float,
            Field(
                ge=10,
                le=900,
                description="How long to wait for the QR scan (and any PIN entry).",
            ),
        ] = 300,
        force: Annotated[
            bool,
            Field(
                description="Ignore the stored session and always show a fresh QR. "
                "A successful login replaces the saved session."
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Log in by scanning a QR code in a window on this machine.

        Call this first. It returns at once when the saved session still works,
        so it is safe to call on every start. Otherwise it opens the
        'Line Desktop MCP' window, shows the QR, and waits up to `timeout_sec`
        for the scan plus any PIN LINE asks for. Closing that window stops the
        wait, in which case nothing is saved.

        The window appears on this machine, so the login cannot be completed
        from a different host. Keep the session file private: it holds the
        access token and the Letter Sealing keys.
        """
        try:
            return run_login(float(timeout_sec), force=bool(force))
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak a raw traceback
            raise ToolError(_describe_failure(exc)) from None
