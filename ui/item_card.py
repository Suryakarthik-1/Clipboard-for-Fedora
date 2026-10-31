"""
ui/item_card.py — Widget for a single clipboard item card.

Displays: preview text / image thumbnail, category badge, source app,
relative timestamp, pin button, and delete button.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QImage, QCursor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QSizePolicy, QToolTip,
)

from storage.models import ClipItem, Category, ContentType

# Category → (emoji, badge object name)
_CAT_ICON: dict[str, tuple[str, str]] = {
    Category.URL:      ("🔗", "BadgeURL"),
    Category.CODE:     ("💻", "BadgeCode"),
    Category.COLOR:    ("🎨", "BadgeColor"),
    Category.FILEPATH: ("📁", "BadgeFilepath"),
    Category.IMAGE:    ("🖼", "BadgeImage"),
    Category.EMAIL:    ("✉", "BadgeEmail"),
    Category.PHONE:    ("📞", "BadgeText"),
    Category.TEXT:     ("📋", "BadgeText"),
}

_THUMBNAIL_SIZE = QSize(60, 40)
_PREVIEW_MAX_CHARS = 80


class ItemCard(QFrame):
    """
    A clickable card representing one ClipItem.

    Signals:
        paste_requested(item)   — user clicked or pressed Enter on this card
        delete_requested(item)  — user clicked the ✕ button
        pin_toggled(item)       — user clicked the pin button
    """

    paste_requested  = pyqtSignal(object)   # ClipItem
    delete_requested = pyqtSignal(object)
    pin_toggled      = pyqtSignal(object)

    def __init__(self, item: ClipItem, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self._selected = False
        self._setup_ui()
        self._apply_item()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self.setObjectName("ItemCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedHeight(64)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(8)

        # Left: thumbnail OR category icon
        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(40, 40)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setObjectName("CardThumb")
        root.addWidget(self._thumb_label)

        # Centre: preview + meta row
        centre = QVBoxLayout()
        centre.setSpacing(2)

        self._preview_label = QLabel()
        self._preview_label.setObjectName("CardPreview")
        self._preview_label.setWordWrap(False)
        self._preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        centre.addWidget(self._preview_label)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)
        self._cat_label  = QLabel()
        self._cat_label.setObjectName("CardCategory")
        self._time_label = QLabel()
        self._time_label.setObjectName("CardMeta")
        self._src_label  = QLabel()
        self._src_label.setObjectName("CardMeta")
        meta_row.addWidget(self._cat_label)
        meta_row.addWidget(self._time_label)
        meta_row.addWidget(self._src_label)
        meta_row.addStretch()
        centre.addLayout(meta_row)

        root.addLayout(centre)

        # Right: pin + delete buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(2)

        self._pin_btn = QPushButton("📌")
        self._pin_btn.setObjectName("CardPin")
        self._pin_btn.setFixedSize(24, 24)
        self._pin_btn.setToolTip("Pin / unpin")
        self._pin_btn.clicked.connect(self._on_pin)

        self._del_btn = QPushButton("✕")
        self._del_btn.setObjectName("CardDelete")
        self._del_btn.setFixedSize(24, 24)
        self._del_btn.setToolTip("Delete")
        self._del_btn.clicked.connect(self._on_delete)

        btn_col.addWidget(self._pin_btn)
        btn_col.addWidget(self._del_btn)
        root.addLayout(btn_col)

    # ── Populate ──────────────────────────────────────────────────────────────

    def _apply_item(self) -> None:
        item = self.item
        icon, badge_name = _CAT_ICON.get(item.category, ("📋", "BadgeText"))

        # Thumbnail / icon
        if item.is_image():
            pix = self._make_thumbnail(item.content)
            if pix:
                self._thumb_label.setPixmap(pix)
            else:
                self._thumb_label.setText("🖼")
        elif item.category == Category.COLOR:
            # Show a colour swatch
            color_text = item.text().strip()
            self._thumb_label.setStyleSheet(
                f"background-color: {color_text}; border-radius: 6px;"
            )
        else:
            self._thumb_label.setText(icon)
            self._thumb_label.setStyleSheet("")

        # Preview text
        preview = item.preview or item.text()[:_PREVIEW_MAX_CHARS]
        if len(preview) > _PREVIEW_MAX_CHARS:
            preview = preview[:_PREVIEW_MAX_CHARS] + "…"
        self._preview_label.setText(preview)
        self._preview_label.setToolTip(item.preview)

        # Category badge
        self._cat_label.setText(item.category)
        self._cat_label.setObjectName(badge_name)

        # Timestamps and source
        self._time_label.setText(item.age_label())
        if item.source_app:
            self._src_label.setText(f"• {item.source_app[:20]}")
        else:
            self._src_label.setText("")

        # Pin button state
        self._pin_btn.setProperty("pinned", item.pinned)
        self._pin_btn.style().unpolish(self._pin_btn)
        self._pin_btn.style().polish(self._pin_btn)

        # Card pinned state
        self.setProperty("pinned", item.pinned)
        self.style().unpolish(self)
        self.style().polish(self)

    @staticmethod
    def _make_thumbnail(png_bytes: bytes) -> QPixmap | None:
        try:
            img = QImage.fromData(png_bytes)
            if img.isNull():
                return None
            pix = QPixmap.fromImage(img)
            return pix.scaled(
                _THUMBNAIL_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        except Exception:
            return None

    # ── Selection ─────────────────────────────────────────────────────────────

    def set_selected(self, value: bool) -> None:
        if self._selected == value:
            return
        self._selected = value
        self.setProperty("selected", value)
        self.style().unpolish(self)
        self.style().polish(self)

    def is_selected(self) -> bool:
        return self._selected

    # ── Events ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.paste_requested.emit(self.item)
        super().mousePressEvent(event)

    def _on_pin(self) -> None:
        self.item.pinned = not self.item.pinned
        self._apply_item()
        self.pin_toggled.emit(self.item)

    def _on_delete(self) -> None:
        self.delete_requested.emit(self.item)

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Call after item data changes (e.g. pinned state updated)."""
        self._apply_item()
