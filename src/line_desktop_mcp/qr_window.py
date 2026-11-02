"""The QR window: a Tk card in a child process, driven over stdin by JSON lines.

Tk must own the process main thread: creating the interpreter on one thread and
letting it be collected on another aborts the whole process with
`Tcl_AsyncDelete: async handler deleted by the wrong thread`. That crash took the
MCP server down, so the window now runs as a tiny child process and this module
-- imported by the server -- only speaks to it. See `main()` for the child side.

Protocol (one JSON object per line, server -> child):

    {"kind": "qr",     "url": "..."}
    {"kind": "pin",    "pin": "..."}
    {"kind": "done",   "message": "..."}
    {"kind": "failed", "message": "..."}
    {"kind": "close"}

The child exits when it is told to close, or when stdin reaches EOF. It exits
with status 2 when Tk cannot start, which is how the server detects "no display"
and falls back to a text QR.
"""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import sys
import threading
from collections.abc import Iterator
from typing import Any

from okline.qrterm import qr_matrix

TITLE = "Line Desktop MCP"

# -- design tokens (identical to the v3 dialog) -----------------------------
BG = "#FFFFFF"
TEXT = "#1A1D1F"
MUTED = "#6B7280"
GRAY = "#9CA3AF"
GREEN = "#06C755"
AMBER = "#F59E0B"
RED = "#EF4444"
FONT = "Segoe UI"

PILL_FONT = (FONT, 10, "bold")
BODY_FONT = (FONT, 11)
SMALL_FONT = (FONT, 9)
PIN_FONT = (FONT, 34, "bold")

CARD_PAD = 22  # horizontal padding around the content box
MIN_CARD = (200, 120)  # floor, so a tiny QR cannot collapse the box
TARGET_QR_PX = 320  # preferred QR side length; the box is reserved at this size
SCALE_FLOOR = 2  # smallest pixels-per-module, so a huge code still fits
SPIN_MS = 80  # spinner frame interval
SPIN_STEP = 30  # degrees per frame
CLOSE_DELAY_MS = 700  # how long a finished window stays readable
POLL_MS = 80  # how often the child drains its queue
READY_TIMEOUT_SEC = 1.5  # how long to wait for the child to fail fast
CENTER_BIAS = 0.45  # < 0.5 sits slightly above the middle of the screen

# state: (pill text, instruction, tone) -- v3's vocabulary, kept so a user who
# has seen the old dialog recognises this one.
STATES: dict[str, tuple[str, str, str]] = {
    "preparing": ("Starting", "Preparing the login...", "gray"),
    "waiting": ("Waiting", "Scan with the LINE app", "green"),
    "pin": ("Enter code", "Enter this code in the LINE app", "amber"),
    "done": ("Done", "", "green"),
    "failed": ("Could not continue", "", "red"),
    "cancelled": ("Cancelled", "", "gray"),
}
TERMINAL_STATES = frozenset({"done", "failed", "cancelled"})
TONE_COLOR = {"green": GREEN, "amber": AMBER, "red": RED, "gray": GRAY}


# -- pure helpers (shared by the child and the tests) ------------------------
def dark_cells(matrix: list[list[bool]]) -> Iterator[tuple[int, int]]:
    """Yield (column, row) of every dark module (pure, testable without Tk)."""
    for row_index, row in enumerate(matrix):
        for column_index, is_dark in enumerate(row):
            if is_dark:
                yield column_index, row_index


def format_pin(pin: str) -> str:
    """Space out PIN digits so they read easily (pure, testable without Tk)."""
    text = (pin or "").strip()
    if len(text) < 2:
        return text
    return " ".join(text)


def scale_for(modules: int) -> int:
    """Pixels per QR module, so the code fits inside TARGET_QR_PX (pure)."""
    if modules <= 0:
        return SCALE_FLOOR
    return max(SCALE_FLOOR, TARGET_QR_PX // modules)


def card_size(image: tuple[int, int] | None, text: tuple[int, int]) -> tuple[int, int]:
    """Content box size, so the window keeps one size in every state.

    The pill, the instruction and the footer sit outside the box, so the
    longest of them would otherwise decide the window width. The box is
    therefore widened until it covers them, and the QR is a floor so it is
    never clipped. Pure: the caller measures the text.
    """
    text_width, text_height = text
    width = max(text_width - 2 * CARD_PAD, MIN_CARD[0])
    height = max(text_height, MIN_CARD[1])
    if image is not None:
        width = max(width, image[0])
        height = max(height, image[1])
    return width, height


def _center_xy(window: tuple[int, int], screen: tuple[int, int]) -> tuple[int, int]:
    """Top-left coordinate that centres a window; bias < 0.5 sits higher."""
    x = int((screen[0] - window[0]) / 2)
    y = int((screen[1] - window[1]) * CENTER_BIAS)
    return max(0, x), max(0, y)


def _enable_dpi() -> None:
    """Make text crisp on scaled displays (Windows only, best effort).

    `ctypes.windll` exists only on Windows, so it is looked up by name: the
    stubs on other platforms leave it out, and mypy runs on Linux in CI.
    """
    with contextlib.suppress(Exception):
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is not None:
            windll.shcore.SetProcessDpiAwareness(1)


# -- server side -------------------------------------------------------------
class QRWindow:
    """Handle to the child window: send it state, notice when the user closes it.

    ``error`` is set when the child could not start at all (no display, or a
    Python build without Tk); callers fall back to rendering the code as text.
    """

    def __init__(self, title: str = TITLE) -> None:
        self._title = title
        self._proc: subprocess.Popen[bytes] | None = None
        self._monitor: threading.Thread | None = None
        self._lock = threading.Lock()
        self._requested_close = False
        #: Set when the user closed the window before the login finished.
        self.cancelled = threading.Event()
        #: Non-None when the window never opened.
        self.error: str | None = None
        #: Last URL handed over, kept so a text fallback can still show it.
        self.last_url: str | None = None

    def start(self) -> None:
        """Launch the child and wait briefly, to notice a window that cannot open."""
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "line_desktop_mcp.qr_window", "--title", self._title],
                stdin=subprocess.PIPE,
                # stdout belongs to the MCP protocol: never let the child near it.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001 - report, then fall back to text
            self.error = f"the QR window could not be started ({exc})"
            return

        with contextlib.suppress(subprocess.TimeoutExpired):
            code = self._proc.wait(timeout=READY_TIMEOUT_SEC)
            self.error = (
                "the QR window could not be opened (no display, or a Python build "
                f"without Tk; the child exited with {code})"
            )
            self._proc = None
            return

        self._monitor = threading.Thread(target=self._watch, name="qr-window", daemon=True)
        self._monitor.start()

    def show_qr(self, url: str) -> None:
        self.last_url = url
        self._send({"kind": "qr", "url": url})

    def show_pin(self, pin: str) -> None:
        self._send({"kind": "pin", "pin": pin})

    def finish(self, message: str = "") -> None:
        """Show the success state; the window closes itself shortly after."""
        self._requested_close = True
        self._send({"kind": "done", "message": message})

    def fail(self, message: str = "") -> None:
        """Show the failure state; the window closes itself shortly after."""
        self._requested_close = True
        self._send({"kind": "failed", "message": message})

    def close(self) -> None:
        self._requested_close = True
        self._send({"kind": "close"})
        timer = threading.Timer(2.0, self._kill)
        timer.daemon = True
        timer.start()

    def join(self, timeout: float | None = None) -> None:
        proc = self._proc
        if proc is None:
            return
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=timeout)

    # -- internals ----------------------------------------------------------
    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        with self._lock:
            try:
                proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass  # the window is gone; the caller's poll sees it

    def _watch(self) -> None:
        proc = self._proc
        if proc is None:
            return
        code = proc.wait()
        if self._requested_close:
            return
        if code == 2:
            # The child gave up on Tk after start() already returned; report it
            # as "cannot open a window" so the caller can fall back to text.
            if self.error is None:
                self.error = (
                    "the QR window could not be opened (no display, or a Python build without Tk)"
                )
            return
        self.cancelled.set()

    def _kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.terminate()


# -- child side --------------------------------------------------------------
class _Ui:
    """The Tk widgets, built and touched only on the child's main thread."""

    def __init__(self, title: str) -> None:
        import tkinter as tk
        from tkinter import font as tkfont

        self._tk = tk
        self._stopped = False
        self._spinning = False
        self._angle = 0
        self._spinner: Any = None
        self._arc: int | None = None
        self._size: tuple[int, int] | None = None
        self._centred = False

        _enable_dpi()
        self.root = tk.Tk()
        self.root.title(title)
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        with contextlib.suppress(Exception):  # scaling is cosmetic
            self.root.tk.call("tk", "scaling", self.root.winfo_fpixels("1i") / 72.0)

        header = tk.Frame(self.root, bg=BG)
        header.pack(side="top", fill="x", padx=CARD_PAD, pady=(18, 0))
        self._pill = tk.Label(header, text="", bg=GREEN, fg=BG, font=PILL_FONT, padx=10, pady=3)
        self._pill.pack(side="right")

        tk.Label(self.root, text="Close to cancel", bg=BG, fg=GRAY, font=SMALL_FONT).pack(
            side="bottom", pady=(0, 14)
        )
        self._instruction = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=BODY_FONT)
        self._instruction.pack(side="bottom", pady=(0, 4))

        self._content = tk.Frame(self.root, bg=BG)
        self._content.pack(side="top", fill="both", expand=True, padx=CARD_PAD, pady=(16, 16))
        self._canvas = tk.Canvas(self._content, background=BG, highlightthickness=0)
        self._pin_label = tk.Label(self._content, bg=BG, fg=TEXT, font=PIN_FONT)

        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._extent = self._text_extent(tkfont)
        self._resize(*card_size((TARGET_QR_PX, TARGET_QR_PX), self._extent))
        self._set_state("preparing")

    def _text_extent(self, tkfont: Any) -> tuple[int, int]:
        try:
            pill = tkfont.Font(family=FONT, size=10, weight="bold")
            body = tkfont.Font(family=FONT, size=11)
            small = tkfont.Font(family=FONT, size=9)
            pin = tkfont.Font(family=FONT, size=34, weight="bold")
            widths = [
                pin.measure(format_pin("123456")),
                small.measure("Close to cancel"),
                *(pill.measure(label) for label, _text, _tone in STATES.values()),
                *(body.measure(text) for _label, text, _tone in STATES.values() if text),
            ]
            heights = [font.metrics("linespace") for font in (pill, body, small, pin)]
            return max(widths), max(heights)
        except Exception:  # noqa: BLE001 - measuring is best effort
            return (460, 120)

    def _resize(self, width: int, height: int) -> None:
        if self._size == (width, height):
            return
        self._size = (width, height)
        self._content.configure(width=width, height=height)
        self._content.pack_propagate(False)
        self._center()

    def _center(self) -> None:
        self.root.update_idletasks()
        x, y = _center_xy(
            (self.root.winfo_width(), self.root.winfo_height()),
            (self.root.winfo_screenwidth(), self.root.winfo_screenheight()),
        )
        self.root.geometry(f"+{x}+{y}")

    def _place_centred(self, widget: Any) -> None:
        for other in (self._canvas, self._pin_label, self._spinner):
            if other is not None and other is not widget:
                other.place_forget()
        if widget is not None:
            widget.place(relx=0.5, rely=0.5, anchor="center")

    def _start_spinner(self) -> None:
        if self._spinner is None:
            side = max(64, min(160, int(min(self._size or MIN_CARD) * 0.35)))
            inset = side * 0.1
            self._spinner = self._tk.Canvas(
                self._content, width=side, height=side, bg=BG, highlightthickness=0
            )
            self._arc = self._spinner.create_arc(
                inset,
                inset,
                side - inset,
                side - inset,
                start=0,
                extent=100,
                style="arc",
                outline=GREEN,
                width=max(2, side // 16),
            )
        self._place_centred(self._spinner)
        self._spinning = True
        self._spin()

    def _spin(self) -> None:
        if self._stopped or not self._spinning or self._spinner is None:
            return
        self._angle = (self._angle + SPIN_STEP) % 360
        self._spinner.itemconfigure(self._arc or 0, start=self._angle)
        self._spinner.after(SPIN_MS, self._spin)

    def _set_state(self, state: str, message: str = "") -> None:
        label, instruction, tone = STATES.get(state, STATES["waiting"])
        self._pill.configure(text=label, bg=TONE_COLOR[tone])
        self._instruction.configure(text=message or instruction)
        self._spinning = False

        if state in TERMINAL_STATES:
            self.root.after(CLOSE_DELAY_MS, self.close)
            return

        if state == "pin":
            self._place_centred(self._pin_label)
        elif state == "preparing":
            self._start_spinner()
        else:
            self._place_centred(self._canvas)

    def draw_qr(self, url: str) -> None:
        matrix = qr_matrix(url)
        scale = scale_for(len(matrix))
        size = len(matrix) * scale
        self._canvas.config(width=size, height=size)
        self._canvas.delete("all")
        for column, row in dark_cells(matrix):
            x0, y0 = column * scale, row * scale
            self._canvas.create_rectangle(x0, y0, x0 + scale, y0 + scale, fill=TEXT, outline="")
        self._resize(*card_size((size, size), self._extent))

    def close(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        with contextlib.suppress(Exception):
            self.root.destroy()


def _read_stdin(messages: queue.Queue[dict[str, Any]]) -> None:
    """Forward the parent's JSON lines to the Tk thread; EOF means "close"."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            messages.put(message)
    messages.put({"kind": "close"})


def main() -> int:
    """Run the window. Returns 2 when Tk is unavailable, so the caller can fall back."""
    title = TITLE
    if "--title" in sys.argv:
        index = sys.argv.index("--title")
        if index + 1 < len(sys.argv):
            title = sys.argv[index + 1] or TITLE

    try:
        ui = _Ui(title)
    except Exception:  # noqa: BLE001 - no display, no Tk: the caller falls back
        return 2

    messages: queue.Queue[dict[str, Any]] = queue.Queue()
    threading.Thread(target=_read_stdin, args=(messages,), name="qr-input", daemon=True).start()

    def pump() -> None:
        if ui._stopped:
            return
        try:
            while True:
                message = messages.get_nowait()
                kind = message.get("kind")
                if kind == "qr":
                    ui.draw_qr(str(message.get("url") or ""))
                    ui._set_state("waiting")
                elif kind == "pin":
                    ui._pin_label.configure(text=format_pin(str(message.get("pin") or "")))
                    ui._set_state("pin")
                elif kind == "done":
                    ui._set_state("done", str(message.get("message") or ""))
                    return
                elif kind == "failed":
                    ui._set_state("failed", str(message.get("message") or ""))
                    return
                elif kind == "close":
                    ui.close()
                    return
        except queue.Empty:
            pass
        ui.root.after(POLL_MS, pump)

    ui.root.after(POLL_MS, pump)
    try:
        ui.root.mainloop()
    finally:
        ui.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
