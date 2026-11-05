"""POSIX permissions for the local state files."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from line_desktop_mcp import paths

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_harden_file_makes_an_existing_file_private(tmp_path: Path) -> None:
    target = tmp_path / "session.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o644)

    paths.harden_file(target)

    assert _mode(target) == 0o600


def test_harden_file_ignores_a_missing_file(tmp_path: Path) -> None:
    paths.harden_file(tmp_path / "missing.json")


def test_harden_dir_makes_a_directory_private(tmp_path: Path) -> None:
    target = tmp_path / "state"
    target.mkdir()
    target.chmod(0o755)

    paths.harden_dir(target)

    assert _mode(target) == 0o700


def test_ensure_private_dir_creates_and_hardens(tmp_path: Path) -> None:
    target = tmp_path / "state" / "nested"

    paths.ensure_private_dir(target)

    assert target.is_dir()
    assert _mode(target) == 0o700
