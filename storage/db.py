"""
storage/db.py — Async SQLite storage for clipboard history.
Uses aiosqlite with WAL mode for safe concurrent access.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite

from storage.models import ClipItem, ContentType, Category

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS clip_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content      BLOB    NOT NULL,
    content_type TEXT    NOT NULL DEFAULT 'text/plain',
    preview      TEXT    NOT NULL DEFAULT '',
    source_app   TEXT    NOT NULL DEFAULT '',
    category     TEXT    NOT NULL DEFAULT 'text',
    pinned       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    last_used_at TEXT    NOT NULL,
    use_count    INTEGER NOT NULL DEFAULT 0,
    checksum     TEXT    NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_created  ON clip_items (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pinned   ON clip_items (pinned DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_checksum ON clip_items (checksum);
"""

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _dt(value: str) -> datetime:
    return datetime.strptime(value, _DT_FMT)


def _dts(value: datetime) -> str:
    return value.strftime(_DT_FMT)


def _row_to_item(row: aiosqlite.Row) -> ClipItem:
    return ClipItem(
        id           = row["id"],
        content      = row["content"],
        content_type = row["content_type"],
        preview      = row["preview"],
        source_app   = row["source_app"],
        category     = row["category"],
        pinned       = bool(row["pinned"]),
        created_at   = _dt(row["created_at"]),
        last_used_at = _dt(row["last_used_at"]),
        use_count    = row["use_count"],
        checksum     = row["checksum"],
    )


# ── Database ──────────────────────────────────────────────────────────────────

class Database:
    """Async clipboard history database."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        logger.info("Database opened: %s", self._path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "Database":
        await self.open()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ── Writes ────────────────────────────────────────────────────────────────

    async def add(self, item: ClipItem) -> ClipItem:
        """
        Insert a new item or refresh its last_used_at if it already exists.
        Returns the item with its db id populated.
        """
        assert self._conn
        now = _dts(datetime.now())

        # Upsert: if checksum exists just bump last_used_at and use_count
        async with self._conn.execute(
            "SELECT id FROM clip_items WHERE checksum = ?", (item.checksum,)
        ) as cur:
            existing = await cur.fetchone()

        if existing:
            await self._conn.execute(
                "UPDATE clip_items SET last_used_at = ?, use_count = use_count + 1 "
                "WHERE id = ?",
                (now, existing["id"]),
            )
            await self._conn.commit()
            item.id = existing["id"]
            return item

        async with self._conn.execute(
            """
            INSERT INTO clip_items
                (content, content_type, preview, source_app, category,
                 pinned, created_at, last_used_at, use_count, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.content,
                item.content_type,
                item.preview,
                item.source_app,
                item.category,
                int(item.pinned),
                now,
                now,
                0,
                item.checksum,
            ),
        ) as cur:
            item.id = cur.lastrowid  # type: ignore[assignment]

        await self._conn.commit()
        return item

    async def delete(self, item_id: int) -> None:
        assert self._conn
        await self._conn.execute(
            "DELETE FROM clip_items WHERE id = ? AND pinned = 0", (item_id,)
        )
        await self._conn.commit()

    async def force_delete(self, item_id: int) -> None:
        """Delete even if pinned."""
        assert self._conn
        await self._conn.execute("DELETE FROM clip_items WHERE id = ?", (item_id,))
        await self._conn.commit()

    async def set_pinned(self, item_id: int, pinned: bool) -> None:
        assert self._conn
        await self._conn.execute(
            "UPDATE clip_items SET pinned = ? WHERE id = ?", (int(pinned), item_id)
        )
        await self._conn.commit()

    async def record_paste(self, item_id: int) -> None:
        assert self._conn
        now = _dts(datetime.now())
        await self._conn.execute(
            "UPDATE clip_items SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
            (now, item_id),
        )
        await self._conn.commit()

    async def clear_unpinned(self) -> int:
        assert self._conn
        async with self._conn.execute(
            "DELETE FROM clip_items WHERE pinned = 0"
        ) as cur:
            count = cur.rowcount
        await self._conn.commit()
        return count  # type: ignore[return-value]

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def all(self, limit: int = 100) -> list[ClipItem]:
        """Return all items: pinned first, then newest first."""
        assert self._conn
        async with self._conn.execute(
            """
            SELECT * FROM clip_items
            ORDER BY pinned DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_item(r) for r in rows]

    async def search(self, query: str, limit: int = 50) -> list[ClipItem]:
        """Simple substring search on preview text."""
        assert self._conn
        pattern = f"%{query}%"
        async with self._conn.execute(
            """
            SELECT * FROM clip_items
            WHERE preview LIKE ? AND content_type != ?
            ORDER BY pinned DESC, created_at DESC
            LIMIT ?
            """,
            (pattern, ContentType.IMAGE, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_item(r) for r in rows]

    async def get_by_id(self, item_id: int) -> Optional[ClipItem]:
        assert self._conn
        async with self._conn.execute(
            "SELECT * FROM clip_items WHERE id = ?", (item_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_item(row) if row else None

    async def count(self) -> int:
        assert self._conn
        async with self._conn.execute("SELECT COUNT(*) FROM clip_items") as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def enforce_limit(self, max_items: int) -> None:
        """Remove oldest unpinned items beyond max_items."""
        assert self._conn
        await self._conn.execute(
            """
            DELETE FROM clip_items
            WHERE id IN (
                SELECT id FROM clip_items
                WHERE pinned = 0
                ORDER BY created_at ASC
                LIMIT MAX(0, (SELECT COUNT(*) FROM clip_items WHERE pinned = 0) - ?)
            )
            """,
            (max_items,),
        )
        await self._conn.commit()
