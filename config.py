"""
config.py — TOML-based configuration management for Clipman.
Reads from ~/.config/clipman/config.toml, writes defaults on first run.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            tomllib = None

# ── Paths ─────────────────────────────────────────────────────────────────────

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "clipman"
DATA_DIR   = Path(os.environ.get("XDG_DATA_HOME",   Path.home() / ".local" / "share")) / "clipman"
CONFIG_FILE = CONFIG_DIR / "config.toml"
DB_PATH     = DATA_DIR / "history.db"
SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "clipman.sock"

DEFAULT_TOML = """\
[general]
max_history       = 100
hotkey            = "super+v"
pause_on_secret   = true
dedup             = true
poll_interval_ms  = 500

[ui]
theme             = "auto"    # auto | dark | light
panel_width       = 440
max_visible_items = 9
show_source_app   = true
show_timestamps   = true
animate           = true

[storage]
db_path           = "{db_path}"
max_image_size_kb = 512
keep_images       = true

[privacy]
ignore_apps       = ["KeePassXC", "keepassxc", "pinentry", "1Password"]
secret_patterns   = [
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
]
"""


@dataclass
class GeneralConfig:
    max_history:      int   = 100
    hotkey:           str   = "super+v"
    pause_on_secret:  bool  = True
    dedup:            bool  = True
    poll_interval_ms: int   = 500


@dataclass
class UIConfig:
    theme:             str  = "auto"
    panel_width:       int  = 440
    max_visible_items: int  = 9
    show_source_app:   bool = True
    show_timestamps:   bool = True
    animate:           bool = True


@dataclass
class StorageConfig:
    db_path:           Path  = field(default_factory=lambda: DB_PATH)
    max_image_size_kb: int   = 512
    keep_images:       bool  = True


@dataclass
class PrivacyConfig:
    ignore_apps:     list = field(default_factory=lambda: [
        "KeePassXC", "keepassxc", "pinentry", "1Password"
    ])
    secret_patterns: list = field(default_factory=lambda: [
        "password", "passwd", "secret", "token", "api_key"
    ])


@dataclass
class AppConfig:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    ui:      UIConfig      = field(default_factory=UIConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)


def _ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _write_defaults() -> None:
    _ensure_dirs()
    content = DEFAULT_TOML.format(db_path=str(DB_PATH))
    CONFIG_FILE.write_text(content, encoding="utf-8")


def load() -> AppConfig:
    """Load config from disk, writing defaults if the file doesn't exist."""
    _ensure_dirs()

    if not CONFIG_FILE.exists():
        _write_defaults()

    if tomllib is None:
        # Fallback: return defaults without parsing
        print("[config] Warning: tomllib/tomli not available, using defaults.")
        return AppConfig()

    try:
        with open(CONFIG_FILE, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        print(f"[config] Failed to parse config: {exc}. Using defaults.")
        return AppConfig()

    cfg = AppConfig()

    g = data.get("general", {})
    cfg.general = GeneralConfig(
        max_history      = g.get("max_history",       cfg.general.max_history),
        hotkey           = g.get("hotkey",             cfg.general.hotkey),
        pause_on_secret  = g.get("pause_on_secret",   cfg.general.pause_on_secret),
        dedup            = g.get("dedup",              cfg.general.dedup),
        poll_interval_ms = g.get("poll_interval_ms",  cfg.general.poll_interval_ms),
    )

    u = data.get("ui", {})
    cfg.ui = UIConfig(
        theme             = u.get("theme",             cfg.ui.theme),
        panel_width       = u.get("panel_width",       cfg.ui.panel_width),
        max_visible_items = u.get("max_visible_items", cfg.ui.max_visible_items),
        show_source_app   = u.get("show_source_app",   cfg.ui.show_source_app),
        show_timestamps   = u.get("show_timestamps",   cfg.ui.show_timestamps),
        animate           = u.get("animate",           cfg.ui.animate),
    )

    s = data.get("storage", {})
    cfg.storage = StorageConfig(
        db_path           = Path(s.get("db_path", str(DB_PATH))),
        max_image_size_kb = s.get("max_image_size_kb", cfg.storage.max_image_size_kb),
        keep_images       = s.get("keep_images",       cfg.storage.keep_images),
    )

    p = data.get("privacy", {})
    cfg.privacy = PrivacyConfig(
        ignore_apps     = p.get("ignore_apps",     cfg.privacy.ignore_apps),
        secret_patterns = p.get("secret_patterns", cfg.privacy.secret_patterns),
    )

    return cfg


# Module-level singleton loaded once at import time
_config: AppConfig | None = None


def get() -> AppConfig:
    global _config
    if _config is None:
        _config = load()
    return _config


def reload() -> AppConfig:
    global _config
    _config = load()
    return _config
