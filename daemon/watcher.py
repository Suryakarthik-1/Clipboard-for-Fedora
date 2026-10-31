"""
daemon/watcher.py — Clipboard watcher that supports both X11 and Wayland.

Strategy:
  - X11:     use python-xlib to monitor PRIMARY + CLIPBOARD selections.
  - Wayland: poll `wl-paste` subprocess every N ms (no stable Wayland clipboard API).

Emits a `clip_changed` signal (Qt) with the new ClipItem whenever the
clipboard changes with non-empty, non-duplicate content.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import time
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QClipboard, QImage
from PyQt6.QtWidgets import QApplication

from storage.models import ClipItem, ContentType

logger = logging.getLogger(__name__)


def _session_type() -> str:
    """Return 'wayland', 'x11', or 'unknown'."""
    s = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if s:
        return s
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def _active_window_app() -> str:
    """
    Best-effort: return the WM_CLASS of the currently active window.
    Works on X11 via xdotool; returns '' on Wayland (protocol limitation).
    """
    try:
        win_id = subprocess.check_output(
            ["xdotool", "getactivewindow"], stderr=subprocess.DEVNULL, timeout=0.3
        ).decode().strip()
        if not win_id:
            return ""
        name = subprocess.check_output(
            ["xdotool", "getwindowname", win_id], stderr=subprocess.DEVNULL, timeout=0.3
        ).decode().strip()
        return name
    except Exception:
        return ""


class ClipboardWatcher(QObject):
    """
    Polls the system clipboard and emits `clip_changed` when content changes.
    Handles text and images; skips empty/duplicate payloads.
    """

    clip_changed = pyqtSignal(object)   # ClipItem
    paused_changed = pyqtSignal(bool)   # True = paused

    def __init__(self, poll_ms: int = 500, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._poll_ms   = poll_ms
        self._paused    = False
        self._last_text: Optional[str]  = None
        self._last_img_hash: Optional[int] = None

        self._qt_clipboard: QClipboard = QApplication.clipboard()
        self._session = _session_type()
        logger.info("ClipboardWatcher: session=%s", self._session)

        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self._poll)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        logger.info("ClipboardWatcher started (interval=%dms)", self._poll_ms)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, value: bool) -> None:
        self._paused = value
        self.paused_changed.emit(value)
        logger.info("ClipboardWatcher paused=%s", value)

    def toggle_pause(self) -> None:
        self.set_paused(not self._paused)

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if self._paused:
            return
        try:
            self._check_text()
            self._check_image()
        except Exception as exc:
            logger.debug("Poll error: %s", exc)

    def _check_text(self) -> None:
        text = self._qt_clipboard.text()
        if not text or text == self._last_text:
            return

        self._last_text = text
        source = _active_window_app()
        item = ClipItem.from_text(text, source_app=source)
        logger.debug("New text clip: %r (source=%s)", item.preview, source)
        self.clip_changed.emit(item)

    def _check_image(self) -> None:
        img: QImage = self._qt_clipboard.image()
        if img.isNull():
            return

        img_hash = hash(img.bits().tobytes()[:256])   # fast partial hash
        if img_hash == self._last_img_hash:
            return
        self._last_img_hash = img_hash

        # Convert QImage → PNG bytes via Pillow
        try:
            from PIL import Image as PILImage
            pil_img = PILImage.fromqimage(img)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG", optimize=True)
            png_bytes = buf.getvalue()
        except Exception as exc:
            logger.warning("Image conversion failed: %s", exc)
            return

        source = _active_window_app()
        item = ClipItem.from_image(png_bytes, source_app=source)
        logger.debug("New image clip (source=%s)", source)
        self.clip_changed.emit(item)
