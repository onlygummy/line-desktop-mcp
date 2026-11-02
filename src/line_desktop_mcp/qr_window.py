"""Tk window that shows the login QR code.

Single responsibility: rendering. The window knows nothing about LINE, tokens
or sessions -- it draws whatever URL it is handed and tells the caller when the
user cancels.

The look follows the v3 dialog this project replaces: a light card with a
coloured status pill, one instruction line, a footer, and a fixed content box
so the QR, the PIN and the spinner all sit in the same place and the window
never jumps between states.

Tk objects may only be touched by the thread that created them, so the window
runs its own thread and every public method just posts a message to a queue.
The window thread is the only one that touches widgets.
"""

from __future__ import annotations

import contextlib
import queue
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
POLL_MS = 80  # how often the window drains its message queue
READY_TIMEOUT_SEC = 5.0
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


def _enable_dpi() -> None:
    """Make text crisp on scaled displays (Windows only, best effort)."""
    with contextlib.suppress(Exception):
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)


def _center_xy(window: tuple[int, int], screen: tuple[int, int]) -> tuple[int, int]:
    """Top-left coordinate that centres a window; bias < 0.5 sits higher."""
    x = int((screen[0] - window[0]) / 2)
    y = int((screen[1] - window[1]) * CENTER_BIAS)
    return max(0, x), max(0, y)


class QRWindow:
    """A small card showing a QR code, a PIN, or an outcome.

    ``error`` is set when the window could not be opened at all (no display, or
    a Python build without Tk); callers fall back to rendering the code as text.
    """

    def __init__(self, title: str = TITLE) -> None:
        self._title = title
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._stopped = False
        #: Set when the user cancels or closes the window.
        self.cancelled = threading.Event()
        #: Non-None when Tk could not start; the window never opened.
        self.error: str | None = None
        #: Last URL handed over, kept so a text fallback can still show it.
        self.last_url: str | None = None
        # Widgets exist only once the window thread created them.
        self._root: Any = None
        self._pill: Any = None
        self._instruction: Any = None
        self._content: Any = None
        self._canvas: Any = None
        self._pin_label: Any = None
        self._spinner: Any = None
        self._tk: Any = None
        self._arc: int | None = None
        self._angle = 0
        self._spinning = False
        self._extent: tuple[int, int] = (0, 0)
        self._size: tuple[int, int] | None = None
        self._centred = False

    # -- producer side (callable from any thread) ---------------------------
    def start(self) -> None:
        """Open the window and wait briefly until it exists (or fails)."""
        self._thread = threading.Thread(target=self._run, name="qr-window", daemon=True)
        self._thread.start()
        self._ready.wait(READY_TIMEOUT_SEC)

    def show_qr(self, url: str) -> None:
        self.last_url = url
        self._queue.put(("qr", url))

    def show_pin(self, pin: str) -> None:
        self._queue.put(("pin", pin))

    def finish(self, message: str = "") -> None:
        """Show the success state; the window closes itself shortly after."""
        self._queue.put(("done", message))

    def fail(self, message: str = "") -> None:
        """Show the failure state; the window closes itself shortly after."""
        self._queue.put(("failed", message))

    def close(self) -> None:
        self._queue.put(("close", ""))

    def join(self, timeout: float | None = None) -> None:
        self._closed.wait(timeout)

    # -- window thread ------------------------------------------------------
    def _run(self) -> None:
        try:
            import tkinter as tk
            from tkinter import font as tkfont
        except Exception as exc:  # noqa: BLE001 - report any import failure
            self.error = f"tkinter is not available in this Python ({exc})"
            self._mark_dead()
            return

        _enable_dpi()
        try:
            root = tk.Tk()
        except Exception as exc:  # noqa: BLE001 - no display, broken Tk, ...
            self.error = f"the QR window could not be opened ({exc})"
            self._mark_dead()
            return

        self._root = root
        self._tk = tk
        root.title(self._title)
        root.attributes("-topmost", True)
        root.resizable(False, False)
        root.configure(bg=BG)
        with contextlib.suppress(Exception):  # scaling is cosmetic
            root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)

        # Header holds only the pill: the app name is already in the title bar.
        header = tk.Frame(root, bg=BG)
        header.pack(side="top", fill="x", padx=CARD_PAD, pady=(18, 0))
        self._pill = tk.Label(header, text="", bg=GREEN, fg=BG, font=PILL_FONT, padx=10, pady=3)
        self._pill.pack(side="right")

        # Bottom-up packing: footer lowest, then the instruction, which leaves
        # the content box the space in between. There is no Cancel button on
        # purpose: the title bar's close control is the one way to stop, and the
        # footer says so.
        tk.Label(root, text="Close to cancel", bg=BG, fg=GRAY, font=SMALL_FONT).pack(
            side="bottom", pady=(0, 14)
        )
        self._instruction = tk.Label(root, text="", bg=BG, fg=MUTED, font=BODY_FONT)
        self._instruction.pack(side="bottom", pady=(0, 4))

        # The content box holds the QR or the PIN. It keeps its size because
        # its children are placed rather than packed.
        self._content = tk.Frame(root, bg=BG)
        self._content.pack(side="top", fill="both", expand=True, padx=CARD_PAD, pady=(16, 16))

        self._canvas = tk.Canvas(self._content, background=BG, highlightthickness=0)
        self._pin_label = tk.Label(self._content, bg=BG, fg=TEXT, font=PIN_FONT)

        root.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._extent = self._text_extent(tkfont)
        # Reserve the final QR box before the code exists: when it arrives the
        # window is already this size, so it never grows in front of the user.
        self._resize(*card_size((TARGET_QR_PX, TARGET_QR_PX), self._extent))
        self._set_state("preparing")

        self._ready.set()
        root.after(POLL_MS, self._pump)
        try:
            root.mainloop()
        finally:
            self._closed.set()

    def _text_extent(self, tkfont: Any) -> tuple[int, int]:
        """Widest and tallest copy shown outside the content box.

        Measured with the same font specs the labels use, so at 150% scaling a
        long instruction cannot push the window wider than the box.
        """
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
        """Fix the content box, re-centring only when the size really changed."""
        if self._size == (width, height):
            return
        self._size = (width, height)
        self._content.configure(width=width, height=height)
        self._content.pack_propagate(False)
        self._center()

    def _center(self) -> None:
        self._root.update_idletasks()
        x, y = _center_xy(
            (self._root.winfo_width(), self._root.winfo_height()),
            (self._root.winfo_screenwidth(), self._root.winfo_screenheight()),
        )
        self._root.geometry(f"+{x}+{y}")

    def _place_centred(self, widget: Any) -> None:
        """Centre one child in the box on both axes, hiding the rest."""
        for other in (self._canvas, self._pin_label, self._spinner):
            if other is not None and other is not widget:
                other.place_forget()
        if widget is not None:
            widget.place(relx=0.5, rely=0.5, anchor="center")

    def _start_spinner(self) -> None:
        """Rotate an arc in the box while the QR is still being created.

        Without it the first seconds show an empty white card, which reads as
        a broken window rather than work in progress.
        """
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

    def _stop_spinner(self) -> None:
        """Stop the arc where it is; hiding it is _place_centred's job."""
        self._spinning = False

    def _set_state(self, state: str, message: str = "") -> None:
        """Render one login state: pill, instruction, and the body it needs."""
        label, instruction, tone = STATES.get(state, STATES["waiting"])
        self._pill.configure(text=label, bg=TONE_COLOR[tone])
        self._instruction.configure(text=message or instruction)
        self._stop_spinner()

        if state in TERMINAL_STATES:
            # Leave the body untouched: the code the user just used stays on
            # screen while the outcome is readable.
            self._root.after(CLOSE_DELAY_MS, self._close)
            return

        if state == "pin":
            self._place_centred(self._pin_label)
        elif state == "preparing":
            self._start_spinner()
        else:
            self._place_centred(self._canvas)

    def _draw_qr(self, url: str) -> None:
        matrix = qr_matrix(url)
        scale = scale_for(len(matrix))
        size = len(matrix) * scale
        self._canvas.config(width=size, height=size)
        self._canvas.delete("all")
        for column, row in dark_cells(matrix):
            x0, y0 = column * scale, row * scale
            self._canvas.create_rectangle(x0, y0, x0 + scale, y0 + scale, fill=TEXT, outline="")
        # The box was reserved for TARGET_QR_PX, so this is normally a no-op.
        self._resize(*card_size((size, size), self._extent))

    def _pump(self) -> None:
        """Drain the queue on the window thread; reschedule while alive."""
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "qr":
                    self._draw_qr(payload)
                    self._set_state("waiting")
                elif kind == "pin":
                    self._pin_label.configure(text=format_pin(payload))
                    self._set_state("pin")
                elif kind == "done":
                    self._set_state("done", payload)
                    return
                elif kind == "failed":
                    self._set_state("failed", payload)
                    return
                elif kind == "close":
                    self._on_cancel()
                    return
        except queue.Empty:
            pass

        if not self._stopped:
            self._root.after(POLL_MS, self._pump)

    def _on_cancel(self) -> None:
        self.cancelled.set()
        self._close()

    def _close(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            self._root.destroy()
        finally:
            self._closed.set()

    def _mark_dead(self) -> None:
        self._stopped = True
        self._ready.set()
        self._closed.set()
