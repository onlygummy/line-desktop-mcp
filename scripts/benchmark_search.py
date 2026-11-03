"""Time archive search at a realistic size, to decide whether FTS5 is needed.

The archive searches with a plain `LIKE` scan. That keeps the schema simple and
needs no index maintenance, but the cost grows with the number of messages.
This script seeds a throwaway archive, runs the shapes of query
`search_messages` issues, and prints milliseconds per query. Run it before
changing the storage layout: a median past roughly 300 ms at a realistic
archive means the scan has grown too slow, and an FTS5 index is the next step.

    uv run python scripts/benchmark_search.py --messages 50000
"""

from __future__ import annotations

import argparse
import random
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from line_desktop_mcp.archive import Archive

# Ordinary chat traffic, with repeats so a common word matches many rows.
ENGLISH_WORDS = [
    "invoice",
    "payment",
    "today",
    "tomorrow",
    "meeting",
    "schedule",
    "report",
    "please",
    "send",
    "the",
    "draft",
    "budget",
    "approval",
    "asap",
    "thanks",
    "lunch",
    "coffee",
    "later",
    "minutes",
    "review",
    "ticket",
    "deploy",
    "project",
    "status",
    "update",
    "client",
    "call",
    "document",
]

THAI_WORDS = ("แอร์ห้อง", "เครื่องซักผ้า", "นัดหมาย", "ใบเสร็จ", "ค่าบริการ", "พรุ่งนี้")

START_MS = 1_700_000_000_000
DAY_MS = 86_400_000
BATCH = 2_000


def _message(index: int, chats: int, rng: random.Random) -> dict[str, Any]:
    """One synthetic row: mostly text, with images and stickers mixed in."""
    text = " ".join(rng.sample(ENGLISH_WORDS, 6)) + " " + rng.choice(THAI_WORDS)
    kind = "text"
    if index % 17 == 0:
        kind, text = "image", None  # an image never has text to match
    elif index % 23 == 0:
        kind, text = "sticker", None
    return {
        "id": f"m{index}",
        "chat_mid": f"c{index % chats}",
        "from_mid": f"u{index % 9}",
        "from_name": f"user {index % 9}",
        "delivered_ms": START_MS + index * 60_000,
        "kind": kind,
        "text": text,
        "sealed": 0,
        "content_type": 0,
        "raw_json": "{}",
    }


def _seed(archive: Archive, count: int, chats: int) -> None:
    """Write `count` synthetic messages in batches, then one rare phrase."""
    rng = random.Random(7)
    batch: list[dict[str, Any]] = []
    for index in range(count):
        batch.append(_message(index, chats, rng))
        if len(batch) == BATCH:
            archive.upsert_messages(batch)
            batch.clear()
    rare = _message(count + 1, chats, rng)
    rare["text"] = "the quarterly revenue report for Q3"
    archive.upsert_messages([*batch, rare])


def _queries(count: int) -> list[tuple[str, dict[str, Any]]]:
    """The query shapes worth timing, cheapest to most expensive."""
    newest = START_MS + count * 60_000
    return [
        ("substring, common word", {"text": "invoice"}),
        ("substring, rare phrase", {"text": "quarterly revenue report"}),
        ("substring, no match", {"text": "zzzzzz"}),
        ("words, two terms apart", {"text": "invoice later", "match": "words"}),
        ("words, phrase", {"text": "quarterly revenue", "match": "words"}),
        ("kind only, no text", {"text": None, "kinds": ["image"]}),
        ("text + last 30 days", {"text": "invoice", "after_ms": newest - 30 * DAY_MS}),
    ]


def _median_ms(store: Archive, query: dict[str, Any], runs: int) -> tuple[float, int]:
    """Median wall time over `runs`, with the row count of the last run."""
    timings: list[float] = []
    hits = 0
    for _ in range(runs):
        started = time.perf_counter()
        rows = store.search(**query)
        timings.append((time.perf_counter() - started) * 1000)
        hits = len(rows)
    return statistics.median(timings), hits


def _size_mb(folder: Path) -> float:
    """Bytes on disk, WAL included: the WAL holds the newest rows."""
    return sum(path.stat().st_size for path in folder.glob("archive.db*")) / (1024 * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages", type=int, default=50_000, help="messages to seed")
    parser.add_argument("--chats", type=int, default=250, help="chats to spread them over")
    parser.add_argument("--runs", type=int, default=3, help="timed runs per query")
    parser.add_argument("--limit", type=int, default=20, help="hits per query")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as folder:
        store = Archive(Path(folder) / "archive.db")
        try:
            started = time.perf_counter()
            _seed(store, args.messages, args.chats)
            seed_ms = (time.perf_counter() - started) * 1000
            print(
                f"archive: {args.messages:,} messages, {args.chats} chats, "
                f"{_size_mb(Path(folder)):.1f} MB (db + wal), seeded in {seed_ms:.0f} ms"
            )
            print(f"{'query':<26} {'hits':>5} {'median ms':>10}")
            for label, query in _queries(args.messages):
                work = dict(query, limit=args.limit)
                median, hits = _median_ms(store, work, max(1, args.runs))
                print(f"{label:<26} {hits:>5} {median:>10.1f}")
        finally:
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
