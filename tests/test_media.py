"""Media helpers: detection, safe names, atomic writes."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from line_desktop_mcp import media

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 8
GIF = b"GIF89a" + b"\x00" * 8
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8


def test_image_ext_recognises_the_formats_a_bubble_can_carry() -> None:
    assert media.image_ext(JPEG) == "jpg"
    assert media.image_ext(PNG) == "png"
    assert media.image_ext(GIF) == "gif"
    assert media.image_ext(WEBP) == "webp"


def test_image_ext_rejects_everything_else() -> None:
    assert media.image_ext(b"%PDF-1.7") is None
    assert media.image_ext(b"") is None


def test_safe_component_cannot_escape_a_directory() -> None:
    assert media.safe_component("../../etc/passwd") == "etc_passwd"
    assert "/" not in media.safe_component("a/b")
    assert "\\" not in media.safe_component("a\\b")


def test_safe_component_never_returns_empty() -> None:
    assert media.safe_component("") == "item"
    assert media.safe_component("...") == "item"


def test_safe_component_keeps_a_normal_mid() -> None:
    assert media.safe_component("u0123456789abcdef") == "u0123456789abcdef"


def test_save_bytes_writes_the_file_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "chat" / "m1.png"

    media.save_bytes(target, PNG)

    assert target.read_bytes() == PNG
    assert list(target.parent.glob("*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_saved_media_is_private(tmp_path: Path) -> None:
    target = tmp_path / "media" / "chat" / "m1.png"

    media.save_bytes(target, PNG)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_sha256_is_stable() -> None:
    assert media.sha256(PNG) == media.sha256(PNG)
    assert media.sha256(PNG) != media.sha256(JPEG)
