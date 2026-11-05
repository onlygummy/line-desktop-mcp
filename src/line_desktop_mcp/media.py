"""Media helpers: identify, name and store downloaded bytes.

Pure functions where possible, so the interesting cases (magic bytes, unsafe
message ids) are testable without network or disk.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from line_desktop_mcp import paths

# Magic-byte prefixes for the formats a LINE image bubble can carry. Order
# matters only for readability; the values are distinct.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


def image_ext(data: bytes) -> str | None:
    """File extension when the bytes are an image we can hand to a model."""
    if not data:
        return None
    for prefix, ext in _MAGIC:
        if data.startswith(prefix):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def safe_component(value: str) -> str:
    """Reduce an id or file name to one safe path component.

    LINE ids are alphanumeric, but `contentMetadata` file names come from the
    sender, so anything that could escape the media directory is stripped
    rather than trusted.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", (value or "").strip())
    cleaned = cleaned.strip("._") or "item"
    return cleaned[:120]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_bytes(path: Path, data: bytes) -> None:
    """Write bytes atomically so a reader never sees a half-written file."""
    paths.ensure_private_dir(path.parent)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    paths.harden_file(temp)
    temp.replace(path)
    paths.harden_file(path)
