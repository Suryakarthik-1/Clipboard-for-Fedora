"""
storage/models.py — ClipItem dataclass, content types, and category detection.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ── Content types ─────────────────────────────────────────────────────────────

class ContentType(str, Enum):
    TEXT  = "text/plain"
    IMAGE = "image/png"
    HTML  = "text/html"
    URIS  = "text/uri-list"


class Category(str, Enum):
    TEXT     = "text"
    URL      = "url"
    CODE     = "code"
    COLOR    = "color"
    FILEPATH = "filepath"
    IMAGE    = "image"
    EMAIL    = "email"
    PHONE    = "phone"


# ── Regex patterns for auto-categorisation ────────────────────────────────────

_RE_URL      = re.compile(r'^https?://\S+$', re.I)
_RE_EMAIL    = re.compile(r'^[\w.+-]+@[\w-]+\.[a-z]{2,}$', re.I)
_RE_PHONE    = re.compile(r'^\+?[\d\s\-().]{7,20}$')
_RE_COLOR    = re.compile(r'^#(?:[0-9a-fA-F]{3}){1,2}$')
_RE_FILEPATH = re.compile(r'^(/[\w.\- ]+)+/?$')

# Heuristic: looks like code if it contains common programming tokens
_RE_CODE     = re.compile(
    r'(def |class |import |from |#include|public |private |function |=>|:=|'
    r'\bconst\b|\blet\b|\bvar\b|<\?php|\bfn\b|package main)',
    re.M,
)


def detect_category(content_type: ContentType, text: str) -> Category:
    """Infer a display category from mime type and text content."""
    if content_type == ContentType.IMAGE:
        return Category.IMAGE
    if content_type == ContentType.URIS:
        return Category.URL

    t = text.strip()

    if _RE_COLOR.match(t):
        return Category.COLOR
    if _RE_URL.match(t):
        return Category.URL
    if _RE_EMAIL.match(t):
        return Category.EMAIL
    if _RE_PHONE.match(t):
        return Category.PHONE
    if _RE_FILEPATH.match(t):
        return Category.FILEPATH
    if _RE_CODE.search(t):
        return Category.CODE

    return Category.TEXT


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_preview(content_type: ContentType, data: bytes, max_len: int = 120) -> str:
    """Build a short human-readable preview string."""
    if content_type == ContentType.IMAGE:
        return "[Image]"
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return "[Binary]"
    # Collapse whitespace for multiline snippets
    collapsed = " ".join(text.split())
    return collapsed[:max_len] + ("…" if len(collapsed) > max_len else "")


# ── ClipItem ──────────────────────────────────────────────────────────────────

@dataclass
class ClipItem:
    """A single entry in the clipboard history."""

    id:           int      = 0
    content:      bytes    = b""
    content_type: str      = ContentType.TEXT
    preview:      str      = ""
    source_app:   str      = ""
    category:     str      = Category.TEXT
    pinned:       bool     = False
    created_at:   datetime = field(default_factory=datetime.now)
    last_used_at: datetime = field(default_factory=datetime.now)
    use_count:    int      = 0
    checksum:     str      = ""

    # ── Constructors ──────────────────────────────────────────────────────────

    @classmethod
    def from_text(cls, text: str, source_app: str = "") -> "ClipItem":
        data = text.encode("utf-8")
        ct   = ContentType.TEXT
        return cls(
            content      = data,
            content_type = ct,
            preview      = _make_preview(ct, data),
            source_app   = source_app,
            category     = detect_category(ct, text),
            checksum     = _checksum(data),
        )

    @classmethod
    def from_image(cls, png_bytes: bytes, source_app: str = "") -> "ClipItem":
        ct = ContentType.IMAGE
        return cls(
            content      = png_bytes,
            content_type = ct,
            preview      = _make_preview(ct, png_bytes),
            source_app   = source_app,
            category     = Category.IMAGE,
            checksum     = _checksum(png_bytes),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def text(self) -> str:
        """Decode content as UTF-8 text (raises for images)."""
        return self.content.decode("utf-8", errors="replace")

    def is_image(self) -> bool:
        return self.content_type == ContentType.IMAGE

    def age_label(self) -> str:
        """Human-readable relative age: '3 min ago', '2 days ago', etc."""
        delta = datetime.now() - self.created_at
        s = int(delta.total_seconds())
        if s < 60:
            return "just now"
        if s < 3600:
            return f"{s // 60} min ago"
        if s < 86400:
            return f"{s // 3600} hr ago"
        return f"{s // 86400} days ago"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ClipItem):
            return NotImplemented
        return self.checksum == other.checksum

    def __hash__(self) -> int:
        return hash(self.checksum)
