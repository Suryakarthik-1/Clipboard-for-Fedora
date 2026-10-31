"""
daemon/sanitizer.py — Detect and suppress sensitive clipboard content.
Checks source app name and text patterns before storing.
"""

from __future__ import annotations

import re
from typing import Sequence


# Patterns that suggest a password/secret was copied
_SECRET_PATTERNS: list[re.Pattern] = [
    re.compile(r'\bpassword\b',    re.I),
    re.compile(r'\bpasswd\b',      re.I),
    re.compile(r'\bsecret\b',      re.I),
    re.compile(r'\bapi[_\s]?key\b',re.I),
    re.compile(r'\btoken\b',       re.I),
    re.compile(r'\bprivate[_\s]?key\b', re.I),
    # Looks like a bcrypt / argon2 hash
    re.compile(r'^\$(?:2[aby]|argon2)\$', re.M),
    # Base64-encoded 32+ byte blob (common secret format)
    re.compile(r'^[A-Za-z0-9+/]{43}={0,2}$'),
]


class Sanitizer:
    """
    Decides whether a clipboard entry should be stored.

    Rules (all configurable):
      - If the source app is in the ignore list → drop it.
      - If the text content matches a secret pattern → drop it
        (only when pause_on_secret is enabled).
    """

    def __init__(
        self,
        ignore_apps: Sequence[str] | None = None,
        extra_patterns: Sequence[str] | None = None,
        pause_on_secret: bool = True,
    ) -> None:
        self._ignore_apps: set[str] = {a.lower() for a in (ignore_apps or [])}
        self._pause_on_secret = pause_on_secret

        self._patterns: list[re.Pattern] = list(_SECRET_PATTERNS)
        for raw in extra_patterns or []:
            try:
                self._patterns.append(re.compile(raw, re.I))
            except re.error:
                pass  # Bad user regex — skip silently

    # ── Public API ────────────────────────────────────────────────────────────

    def should_store(self, text: str, source_app: str) -> bool:
        """Return False if this clipboard entry should be silently dropped."""
        if self._is_ignored_app(source_app):
            return False
        if self._pause_on_secret and self._looks_secret(text):
            return False
        return True

    def update_from_config(
        self,
        ignore_apps: Sequence[str],
        extra_patterns: Sequence[str],
        pause_on_secret: bool,
    ) -> None:
        self._ignore_apps = {a.lower() for a in ignore_apps}
        self._pause_on_secret = pause_on_secret
        self._patterns = list(_SECRET_PATTERNS)
        for raw in extra_patterns:
            try:
                self._patterns.append(re.compile(raw, re.I))
            except re.error:
                pass

    # ── Internal ──────────────────────────────────────────────────────────────

    def _is_ignored_app(self, source_app: str) -> bool:
        return source_app.lower() in self._ignore_apps

    def _looks_secret(self, text: str) -> bool:
        for pat in self._patterns:
            if pat.search(text):
                return True
        return False
