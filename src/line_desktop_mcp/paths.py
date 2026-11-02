"""Where the server keeps its files (single responsibility: paths).

Everything lives in one directory so a user can find, back up or delete the
whole local state in one move. Each path can be overridden separately.
"""

from __future__ import annotations

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
