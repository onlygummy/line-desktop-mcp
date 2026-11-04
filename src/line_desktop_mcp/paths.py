"""Where the server keeps its files (single responsibility: paths).

Everything lives in one directory so a user can find, back up or delete the
whole local state in one move. Each path can be overridden separately.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

HOME_ENV = "LINE_DESKTOP_MCP_HOME"
SESSION_ENV = "LINE_DESKTOP_MCP_SESSION"
DB_ENV = "LINE_DESKTOP_MCP_DB"
MEDIA_ENV = "LINE_DESKTOP_MCP_MEDIA"


def _override(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else None


def home_dir() -> Path:
    """Directory holding the session, the archive database and the media backup."""
    override = _override(HOME_ENV)
    if override is not None:
        return override
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "line-desktop-mcp"
    return Path.home() / ".local" / "share" / "line-desktop-mcp"


def session_path() -> Path:
    """Credentials: access token, refresh token and the E2EE keychain."""
    return _override(SESSION_ENV) or home_dir() / "session.json"


def archive_path() -> Path:
    """SQLite database holding every message this server has read."""
    return _override(DB_ENV) or home_dir() / "archive.db"


def media_dir() -> Path:
    """Backup of downloaded media, one directory per chat."""
    return _override(MEDIA_ENV) or home_dir() / "media"


def harden_dir(path: Path) -> None:
    """Make a state directory private to the current user on POSIX.

    Windows uses per-user ACLs under ``%LOCALAPPDATA%`` already; ``chmod``
    there only toggles the read-only bit, so it is deliberately a no-op.
    """
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        path.chmod(0o700)


def harden_file(path: Path) -> None:
    """Make a state file private to the current user on POSIX.

    A missing file is not an error: the caller may be hardening a path during
    logout or before a login has created it.
    """
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` and make it private on POSIX."""
    path.mkdir(parents=True, exist_ok=True)
    harden_dir(path)
    return path
