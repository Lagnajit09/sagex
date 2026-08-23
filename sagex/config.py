"""Persistent user settings, stored at ~/.sagex/config.json.

A tiny load/save layer over a JSON file. Deliberately fail-soft: a missing or
corrupt file falls back to defaults, and a failed write never crashes the app —
losing a preference is not worth interrupting the user.
"""

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".sagex"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Every setting the app understands, with its default value.
DEFAULTS = {
    "shell": None,        # None = auto-detect the default shell
}


def load() -> dict:
    """Return settings (file values merged over defaults). Never raises."""
    settings = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            settings.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass                          # missing/broken file -> just use defaults
    return settings


def save(settings: dict) -> None:
    """Write settings to disk, creating ~/.sagex if needed. Best-effort."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass                          # can't write? oh well — don't crash
