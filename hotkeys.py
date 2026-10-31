"""
hotkeys.py — Global hotkey registration for Clipman.

Uses pynput to listen for Super+V (or any configured combo).
Emits a Qt signal so the UI can show the panel from the main thread.

Works on X11. On Wayland, pynput falls back to /dev/input via evdev
which requires the user to be in the 'input' group.
"""

from __future__ import annotations

import logging
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal
from pynput import keyboard

logger = logging.getLogger(__name__)

# Map friendly names → pynput Key enum values
_SPECIAL: dict[str, keyboard.Key] = {
    "super":  keyboard.Key.cmd,
    "ctrl":   keyboard.Key.ctrl,
    "alt":    keyboard.Key.alt,
    "shift":  keyboard.Key.shift,
    "tab":    keyboard.Key.tab,
    "space":  keyboard.Key.space,
    "esc":    keyboard.Key.esc,
    "enter":  keyboard.Key.enter,
    "up":     keyboard.Key.up,
    "down":   keyboard.Key.down,
    "left":   keyboard.Key.left,
    "right":  keyboard.Key.right,
}


def _parse_hotkey(combo: str) -> set:
    """
    Parse 'super+v' → frozenset of pynput key objects.
    Raises ValueError on unknown tokens.
    """
    keys: set = set()
    for part in combo.lower().split("+"):
        part = part.strip()
        if part in _SPECIAL:
            keys.add(_SPECIAL[part])
        elif len(part) == 1:
            keys.add(keyboard.KeyCode.from_char(part))
        else:
            raise ValueError(f"Unknown key token: {part!r}")
    return keys


class HotkeyManager(QObject):
    """
    Listens for a global hotkey in a background thread and emits
    `activated` on the Qt event loop when it fires.
    """

    activated = pyqtSignal()

    def __init__(self, combo: str = "super+v", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._combo    = combo
        self._keys     = _parse_hotkey(combo)
        self._pressed: set = set()
        self._listener: keyboard.Listener | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press   = self._on_press,
            on_release = self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        logger.info("HotkeyManager listening for: %s", self._combo)

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    def set_combo(self, combo: str) -> None:
        """Reconfigure the hotkey at runtime."""
        self._keys   = _parse_hotkey(combo)
        self._combo  = combo
        self._pressed.clear()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _normalise(self, key) -> object:
        """Collapse left/right modifier variants to the canonical form."""
        try:
            # e.g. Key.ctrl_l → Key.ctrl
            return key.value if hasattr(key, "value") else key
        except Exception:
            return key

    def _on_press(self, key) -> None:
        self._pressed.add(self._normalise(key))
        if self._keys and self._keys.issubset(self._pressed):
            logger.debug("Hotkey fired: %s", self._combo)
            self.activated.emit()

    def _on_release(self, key) -> None:
        self._pressed.discard(self._normalise(key))
