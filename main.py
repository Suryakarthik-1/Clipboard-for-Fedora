"""
main.py — Entry point for Clipman clipboard manager.

Initializes Qt application, creates tray icon, starts clipboard watcher,
and manages the global hotkey. Handles both X11 and Wayland sessions.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMessageBox
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtCore import QTimer, QObject, pyqtSignal

from config import get as get_config
from daemon.watcher import ClipboardWatcher
from daemon.sanitizer import Sanitizer
from storage.db import Database
from ui.panel import ClipmanPanel
from hotkeys import HotkeyManager

logger = logging.getLogger(__name__)


class AppController(QObject):
    """
    Main application controller. Manages the tray icon, panel,
    and lifecycle events.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._config = get_config()
        self._tray: QSystemTrayIcon | None = None
        self._panel: ClipmanPanel | None = None
        self._watcher: ClipboardWatcher | None = None
        self._hotkey: HotkeyManager | None = None
        self._db_path = Path(self._config.storage.db_path)
        self._paused = False

    # ── Initialization ────────────────────────────────────────────────────────

    def initialize(self) -> bool:
        """Create UI, start watchers, and set up tray. Returns False on fatal error."""
        try:
            # Setup logging
            level = logging.DEBUG if "--debug" in sys.argv else logging.INFO
            logging.basicConfig(
                level=level,
                format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            )
            logger.info("Clipman starting")

            # Database setup
            self._init_db()

            # Sanitizer with config
            sanitizer = Sanitizer(
                ignore_apps=self._config.privacy.ignore_apps,
                extra_patterns=self._config.privacy.secret_patterns,
                pause_on_secret=self._config.general.pause_on_secret,
            )

            # Clipboard watcher
            self._watcher = ClipboardWatcher(
                poll_ms=self._config.general.poll_interval_ms,
                parent=self,
            )
            self._watcher.clip_changed.connect(self._on_clip_change)
            self._watcher.paused_changed.connect(self._on_pause_change)
            self._watcher.start()

            # Hotkey manager
            self._hotkey = HotkeyManager(
                combo=self._config.general.hotkey,
                parent=self,
            )
            self._hotkey.activated.connect(self._on_hotkey)
            self._hotkey.start()

            # Tray icon
            self._setup_tray()

            # Panel
            self._panel = ClipmanPanel(
                width=self._config.ui.panel_width,
                max_items=self._config.ui.max_visible_items,
                parent=self,
            )
            self._panel.paste_item.connect(self._on_paste_request)
            self._panel.item_deleted.connect(self._on_item_deleted)
            self._panel.item_pinned.connect(self._on_item_pinned)
            self._panel.clear_requested.connect(self._on_clear)
            self._panel.pause_toggled.connect(self._on_pause_toggle)
            self._panel.settings_opened.connect(self._open_settings)

            # Theme handling
            self._apply_theme(self._config.ui.theme)

            # Show tray
            if self._tray:
                self._tray.show()

            logger.info("Clipman initialized successfully")
            return True

        except Exception as exc:
            logger.error("Initialization failed: %s", exc, exc_info=True)
            QMessageBox.critical(
                None, "Clipman Error", f"Failed to start: {exc}\n\nSee terminal for details."
            )
            return False

    def _init_db(self) -> None:
        """Initialize SQLite database and enforce retention limits."""
        async def run():
            async with Database(self._db_path) as db:
                await db.enforce_limit(self._config.general.max_history)
        asyncio.run(run())

    def _setup_tray(self) -> None:
        """Create system tray icon with context menu."""
        icon_path = Path(__file__).parent / "assets" / "icon.svg"
        if not icon_path.is_file():
            # Fallback to generic clipboard icon if SVG missing
            icon_path = Path(__file__).parent / "assets" / "icon.png"
        if not icon_path.is_file():
            # Last resort: use Qt built-in icon
            self._tray = QSystemTrayIcon(QIcon.fromTheme("clipboard"))
        else:
            self._tray = QSystemTrayIcon(QIcon(str(icon_path)))

        # Menu
        menu = self._tray.contextMenu()
        menu.addAction("Open Clipboard History").triggered.connect(self._show_panel)
        menu.addAction("Pause Recording").triggered.connect(self._toggle_pause)
        menu.addAction("Settings").triggered.connect(self._open_settings)
        menu.addSeparator()
        menu.addAction("About Clipman").triggered.connect(self._show_about)
        menu.addAction("Quit").triggered.connect(QApplication.quit)
        self._tray.setContextMenu(menu)

    def _apply_theme(self, theme: str) -> None:
        """Load and apply Qt style sheet based on theme setting."""
        if theme == "auto":
            # Detect GNOME/KDE theme (simplified)
            try:
                import gi
                gi.require_version("Gio", "2.0")
                from gi.repository import Gio
                settings = Gio.Settings(
                    schema_id="org.gnome.desktop.interface"
                )
                theme_name = settings.get_string("gtk-theme-name")
                if "dark" in theme_name.lower():
                    theme = "dark"
                else:
                    theme = "light"
            except Exception:
                # Fallback to light if detection fails
                theme = "light"

        style_file = Path(__file__).parent / "ui" / "themes" / f"{theme}.qss"
        if style_file.is_file():
            QApplication.setStyleSheet(style_file.read_text())

    # ── Event handlers ────────────────────────────────────────────────────────────

    def _on_clip_change(self, item) -> None:
        """New clipboard item arrived — store it (async)."""
        async def store():
            async with Database(self._db_path) as db:
                sanitized = item
                # Run sanitizer logic synchronously
                if not Sanitizer().should_store(item.text(), item.source_app):
                    logger.debug("Skipped sensitive clipboard content")
                    return
                await db.add(sanitized)
        asyncio.create_task(store())

    def _on_pause_change(self, paused: bool) -> None:
        self._paused = paused
        if self._panel:
            self._panel.set_paused(paused)

    def _on_hotkey(self) -> None:
        """Global hotkey pressed — show panel."""
        if self._panel:
            self._panel.show_at_cursor()

    def _on_paste_request(self, item) -> None:
        """User requested to paste an item via the panel."""
        async def do_paste():
            # Update last_used in DB
            async with Database(self._db_path) as db:
                await db.record_paste(item.id)
            # Actually paste to system clipboard
            if item.is_image():
                import base64
                from PIL import Image as PILImage
                import io
                img_bytes = base64.b64decode(item.content)
                pil_img = PILImage.open(io.BytesIO(img_bytes))
                img = QPixmap.fromImage(pil_img.convert("RGBA"))
                QApplication.clipboard().setImage(img)
            else:
                QApplication.clipboard().setText(item.text())
            # Auto-paste if configured
            if item.text().strip():
                import subprocess
                try:
                    subprocess.run(["xdotool", "key", "Ctrl+v"], check=False)
                except Exception:
                    pass
        asyncio.create_task(do_paste())

    def _on_item_deleted(self, item_id: int) -> None:
        async def delete():
            async with Database(self._db_path) as db:
                await db.delete(item_id)
        asyncio.create_task(delete())

    def _on_item_pinned(self, item_id: int, pinned: bool) -> None:
        async def pin():
            async with Database(self._db_path) as db:
                await db.set_pinned(item_id, pinned)
        asyncio.create_task(pin())

    def _on_clear(self) -> None:
        async def clear():
            async with Database(self._db_path) as db:
                await db.clear_unpinned()
        asyncio.create_task(clear())

    def _on_pause_toggle(self) -> None:
        self._watcher.toggle_pause()

    def _show_panel(self) -> None:
        if self._panel:
            self._panel.show_at_cursor()

    def _toggle_pause(self) -> None:
        self._watcher.toggle_pause()

    def _open_settings(self) -> None:
        import subprocess
        try:
            subprocess.Popen(["xdg-open", "~/.config/clipman/config.toml"])
        except Exception:
            QMessageBox.information(None, "Settings", "Open ~/.config/clipman/config.toml manually")

    def _show_about(self) -> None:
        QMessageBox.about(
            None,
            "Clipman — Windows-style Clipboard Manager",
            "Version 0.1.0\n\nA clipboard manager for Fedora Linux\n\n© 2026 Clipman contributors\n\nSource: https://github.com/<username>/clipman",
        )

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Graceful shutdown of all resources."""
        if self._watcher:
            self._watcher.stop()
        if self._hotkey:
            self._hotkey.stop()
        if self._panel:
            self._panel.hide()
        if self._tray:
            self._tray.hide()


def main() -> None:
    """Application entry point."""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Set high-DPI scaling attributes for HiDPI displays
    os.environ["QT_FONT_DPI"] = "96"
    app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling)
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)

    controller = AppController()
    if not controller.initialize():
        sys.exit(1)

    # Handle graceful shutdown
    app.aboutToQuit.connect(controller.cleanup)

    # Run the Qt event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
