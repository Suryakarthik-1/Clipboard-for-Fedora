"""
ui/panel.py — Main clipboard history popup panel (the Win+V equivalent).

Behaviour:
  - Appears at cursor position (or screen centre as fallback)
  - Frameless, rounded corners, drop shadow
  - Search bar at top, scrollable card list below, toolbar at bottom
  - Keyboard: ↑/↓ navigate, Enter paste, Delete remove, Esc close, Ctrl+F search
  - Closes when it loses focus
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from PyQt6.QtCore import (
    Qt, QTimer, QPoint, QRect, pyqtSignal, QPropertyAnimation,
    QEasingCurve, QSize,
)
from PyQt6.QtGui import (
    QKeyEvent, QFocusEvent, QScreen, QCursor, QIcon,
    QColor, QPainter, QPainterPath,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QLineEdit, QPushButton, QFrame, QSizePolicy,
    QGraphicsDropShadowEffect, QApplication,
)

from storage.models import ClipItem
from ui.item_card import ItemCard

logger = logging.getLogger(__name__)


class SearchBar(QLineEdit):
    """Search input with magnifier icon prefix."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SearchBar")
        self.setPlaceholderText("Search clipboard history…")
        self.setClearButtonEnabled(True)


class SectionLabel(QLabel):
    """Divider label for pinned / today / older sections."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text.upper(), parent)
        self.setObjectName("SectionHeader")


class ClipmanPanel(QWidget):
    """
    The main floating clipboard history panel.

    Signals:
        paste_item(item)    — request to write item back to clipboard + auto-paste
        item_deleted(id)    — request to remove item from DB
        item_pinned(id, pinned)
        clear_requested()
        pause_toggled()
        settings_opened()
    """

    paste_item      = pyqtSignal(object)   # ClipItem
    item_deleted    = pyqtSignal(int)      # item id
    item_pinned     = pyqtSignal(int, bool)
    clear_requested = pyqtSignal()
    pause_toggled   = pyqtSignal()
    settings_opened = pyqtSignal()

    def __init__(self, width: int = 440, max_items: int = 9, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self._panel_width   = width
        self._max_visible   = max_items
        self._cards: list[ItemCard] = []
        self._selected_idx: int = -1
        self._paused: bool = False

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Clipman")
        self._setup_ui()
        self._add_shadow()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self.setFixedWidth(self._panel_width)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)   # room for drop shadow
        outer.setSpacing(0)

        # Inner container with the visible background
        self._container = QWidget()
        self._container.setObjectName("ClipmanPanel")
        outer.addWidget(self._container)

        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(8, 8, 8, 0)
        layout.setSpacing(4)

        # ── Header row (search + settings) ────────────────────────────────
        header = QHBoxLayout()
        self._search = SearchBar()
        self._search.textChanged.connect(self._on_search)
        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setObjectName("BtnSettings")
        self._settings_btn.setFixedSize(28, 28)
        self._settings_btn.setToolTip("Settings")
        self._settings_btn.clicked.connect(self.settings_opened)
        header.addWidget(self._search)
        header.addWidget(self._settings_btn)
        layout.addLayout(header)

        # ── Scrollable item list ───────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setObjectName("ItemList")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()

        self._empty_label = QLabel("No clipboard history yet.\nCopy something to get started!")
        self._empty_label.setObjectName("EmptyLabel")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setVisible(False)
        self._list_layout.insertWidget(0, self._empty_label)

        self._scroll.setWidget(self._list_widget)
        layout.addWidget(self._scroll)

        # ── Toolbar ────────────────────────────────────────────────────────
        toolbar = QWidget()
        toolbar.setObjectName("Toolbar")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 6, 8, 6)

        self._clear_btn = QPushButton("Clear all")
        self._clear_btn.setObjectName("BtnClearAll")
        self._clear_btn.clicked.connect(self._on_clear)

        self._pause_btn = QPushButton("⏸ Pause")
        self._pause_btn.setObjectName("BtnPause")
        self._pause_btn.setCheckable(False)
        self._pause_btn.clicked.connect(self._on_pause)

        tb_layout.addWidget(self._clear_btn)
        tb_layout.addStretch()
        tb_layout.addWidget(self._pause_btn)

        layout.addWidget(toolbar)

    def _add_shadow(self) -> None:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 120))
        self._container.setGraphicsEffect(shadow)

    # ── Public API ────────────────────────────────────────────────────────────

    def populate(self, items: list[ClipItem]) -> None:
        """Replace the card list with a fresh set of items."""
        self._clear_cards()
        self._selected_idx = -1

        if not items:
            self._empty_label.setVisible(True)
            self._resize_to_content(0)
            return

        self._empty_label.setVisible(False)

        # Group items into pinned / today / older
        from datetime import datetime, timedelta
        now    = datetime.now()
        today  = now.replace(hour=0, minute=0, second=0, microsecond=0)
        pinned = [i for i in items if i.pinned]
        rest   = [i for i in items if not i.pinned]
        todays = [i for i in rest if i.created_at >= today]
        older  = [i for i in rest if i.created_at < today]

        def add_section(label: str, section_items: list[ClipItem]) -> None:
            if not section_items:
                return
            lbl = SectionLabel(label)
            self._list_layout.insertWidget(self._list_layout.count() - 1, lbl)
            for item in section_items:
                card = ItemCard(item)
                card.paste_requested.connect(self._on_paste)
                card.delete_requested.connect(self._on_delete)
                card.pin_toggled.connect(self._on_pin)
                self._list_layout.insertWidget(self._list_layout.count() - 1, card)
                self._cards.append(card)

        add_section("📌 Pinned", pinned)
        add_section("Today",    todays)
        add_section("Older",    older)

        self._resize_to_content(len(self._cards))

        # Auto-select first item
        if self._cards:
            self._select(0)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if paused:
            self._pause_btn.setText("▶ Resume")
            self._pause_btn.setProperty("paused", True)
        else:
            self._pause_btn.setText("⏸ Pause")
            self._pause_btn.setProperty("paused", False)
        self._pause_btn.style().unpolish(self._pause_btn)
        self._pause_btn.style().polish(self._pause_btn)

    def show_at_cursor(self) -> None:
        """Position the panel near the mouse cursor, keeping it on screen."""
        self._search.clear()
        cursor_pos = QCursor.pos()
        screen: QScreen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        geo: QRect = screen.availableGeometry()

        x = cursor_pos.x() - self.width() // 2
        y = cursor_pos.y() - 30   # slightly above cursor

        # Clamp to screen
        x = max(geo.left() + 8, min(x, geo.right() - self.width() - 8))
        y = max(geo.top() + 8,  min(y, geo.bottom() - self.height() - 8))

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._search.setFocus()

    # ── Resize helper ─────────────────────────────────────────────────────────

    def _resize_to_content(self, n_cards: int) -> None:
        """Resize the panel height to fit visible cards + chrome."""
        card_h     = 64
        spacing    = 2
        chrome_h   = 44 + 44 + 16   # header + toolbar + margins
        section_h  = 22              # per section header
        n_sections = 3               # max 3 sections
        visible    = min(n_cards, self._max_visible)
        h = chrome_h + visible * (card_h + spacing) + n_sections * section_h + 40
        h = max(h, 180)
        self.setFixedHeight(h)

    # ── Card management ───────────────────────────────────────────────────────

    def _clear_cards(self) -> None:
        for card in self._cards:
            card.deleteLater()
        self._cards.clear()
        # Remove section labels too
        while self._list_layout.count() > 1:   # keep the stretch
            item = self._list_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        # Re-add empty label
        self._list_layout.insertWidget(0, self._empty_label)

    def _select(self, idx: int) -> None:
        if not self._cards:
            return
        idx = max(0, min(idx, len(self._cards) - 1))
        if self._selected_idx >= 0 and self._selected_idx < len(self._cards):
            self._cards[self._selected_idx].set_selected(False)
        self._selected_idx = idx
        self._cards[idx].set_selected(True)
        # Scroll the card into view
        self._scroll.ensureWidgetVisible(self._cards[idx])

    def _on_search(self, text: str) -> None:
        # Filter cards by showing/hiding based on preview match
        text = text.lower()
        for card in self._cards:
            match = not text or text in card.item.preview.lower()
            card.setVisible(match)

    # ── Slot handlers ─────────────────────────────────────────────────────────

    def _on_paste(self, item: ClipItem) -> None:
        self.paste_item.emit(item)
        self.hide()

    def _on_delete(self, item: ClipItem) -> None:
        self.item_deleted.emit(item.id)
        # Remove card from list
        for i, card in enumerate(self._cards):
            if card.item.id == item.id:
                card.deleteLater()
                self._cards.pop(i)
                if self._selected_idx >= len(self._cards):
                    self._selected_idx = len(self._cards) - 1
                break
        if not self._cards:
            self._empty_label.setVisible(True)

    def _on_pin(self, item: ClipItem) -> None:
        self.item_pinned.emit(item.id, item.pinned)

    def _on_clear(self) -> None:
        self.clear_requested.emit()
        self._clear_cards()
        self._empty_label.setVisible(True)

    def _on_pause(self) -> None:
        self.pause_toggled.emit()

    # ── Keyboard navigation ───────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()

        if key == Qt.Key.Key_Escape:
            self.hide()

        elif key in (Qt.Key.Key_Up, Qt.Key.Key_K):
            self._select(self._selected_idx - 1)

        elif key in (Qt.Key.Key_Down, Qt.Key.Key_J):
            self._select(self._selected_idx + 1)

        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if 0 <= self._selected_idx < len(self._cards):
                self._on_paste(self._cards[self._selected_idx].item)

        elif key == Qt.Key.Key_Delete:
            if 0 <= self._selected_idx < len(self._cards):
                self._on_delete(self._cards[self._selected_idx].item)

        elif key == Qt.Key.Key_F and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._search.setFocus()
            self._search.selectAll()

        elif key == Qt.Key.Key_P and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if 0 <= self._selected_idx < len(self._cards):
                self._on_pin(self._cards[self._selected_idx].item)

        else:
            # Forward typing to search bar
            if not self._search.hasFocus() and event.text():
                self._search.setFocus()
                self._search.setText(event.text())
            super().keyPressEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        # Hide if focus moves to a non-child widget
        focused = QApplication.focusWidget()
        if focused is None or not self.isAncestorOf(focused):
            self.hide()
        super().focusOutEvent(event)
