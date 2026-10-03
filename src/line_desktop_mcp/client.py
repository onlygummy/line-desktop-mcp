"""Shared LineClient for the whole server process (single responsibility).

One client for the server lifetime, not one per tool call. `line-ext-msg`
caches the ready page on the client instance, so a client that already ran
the readiness checks answers later queries straight from the cached tab
instead of repeating them. The `line-ext-msg` CLI works the same way, and
matching it keeps the login wait to a single event instead of one per call.

The lock is not optional. That cached page is one browser tab, so two
overlapping tool calls would navigate away from each other mid-read. The lock
serialises them, and because the second call reuses the page the first one
left behind, a call that arrives while the QR dialog is still open simply
proceeds once login finishes.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from line_ext_msg import LineClient

# Reentrant so reset() stays callable from inside a shared_client() block.
_lock = threading.RLock()
_client: LineClient | None = None


def reset() -> None:
    """Forget the shared client so the next call builds a fresh one.

    Required after `logout()`, which stops the debug Chrome, and called by
    `line_status` before every readiness check because `status()` starts a
    new Playwright instance and reusing a client that already holds one
    leaks the old connection. Also used by the tests so a stubbed client
    never leaks into the next case.
    """
    global _client
    with _lock:
        if _client is not None:
            _client.close()
        _client = None


@contextmanager
def shared_client() -> Iterator[LineClient]:
    """Yield the process-wide client, holding the lock for the whole block.

    Any exception drops the client on the way out. The usual cause is a dead
    tab: a cached page cannot outlive its Chrome, and the library only rebuilds
    the page inside `status()`, which a cached page skips. Dropping the client
    makes the next call re-run the readiness checks instead of reusing a tab
    that is gone. The original error is re-raised untouched so a real bug is
    never disguised as a browser problem.
    """
    global _client
    with _lock:
        if _client is None:
            _client = LineClient(quiet=True)
        try:
            yield _client
        except Exception:
            _client.close()
            _client = None
            raise
