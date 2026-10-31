# Clipman — Windows-style Clipboard Manager for Linux

A feature-rich clipboard manager for Fedora Linux inspired by Windows 11's `Win+V` clipboard history.

## Features

- **`Super+V` hotkey** — instant clipboard history popup
- **Persistent history** — survives reboots, stored in SQLite
- **Images + Text** — handles text, images, URLs, code, file paths
- **Pin items** — keep important clips forever
- **Fuzzy search** — find anything in your history fast
- **Auto-categories** — URLs, code, colors (#hex), file paths, plain text
- **Source app tracking** — see which app you copied from
- **Deduplication** — same content stored only once
- **Secret/Pause mode** — stop recording (for passwords)
- **System tray** — lives in your tray, always ready
- **Dark/Light theme** — follows your GNOME theme automatically
- **X11 + Wayland** — works on both display servers

## Install

```bash
# Install dependencies
pip install -r requirements.txt

# Run directly
python3 main.py

# Install as systemd user service (autostart)
make install
```

## Usage

| Key | Action |
|-----|--------|
| `Super+V` | Open clipboard history |
| `↑ ↓` | Navigate items |
| `Enter` | Paste selected item |
| `Delete` | Remove selected item |
| `Escape` | Close panel |
| `Ctrl+F` | Focus search |

Right-click any item for: Pin, Copy, Delete, Copy as plain text.

## Config

Edit `~/.config/clipman/config.toml`:

```toml
[general]
max_history = 100
hotkey = "super+v"
pause_on_secret = true

[ui]
theme = "auto"   # auto | dark | light
panel_width = 420
max_visible_items = 8

[storage]
db_path = "~/.local/share/clipman/history.db"
max_image_size_kb = 512
```

## Architecture

```
main.py                  — bootstrap, tray, IPC
daemon/watcher.py        — clipboard polling (X11 + Wayland)
daemon/ipc.py            — Unix socket IPC
daemon/sanitizer.py      — strip sensitive patterns
storage/db.py            — SQLite async storage
storage/models.py        — ClipItem dataclass
ui/panel.py              — main Win+V popup
ui/item_card.py          — clip card widget
ui/search_bar.py         — fuzzy search
ui/tray.py               — system tray
ui/settings.py           — settings dialog
hotkeys.py               — global hotkey registration
config.py                — TOML config management
```

## Requirements

- Python 3.11+
- PyQt6
- Fedora 38+ (X11 or Wayland)
# Clipboard-for-Fedora
