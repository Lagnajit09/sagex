"""Persistent user settings, stored at ~/.sagex/config.json.

A tiny load/save layer over a JSON file. Deliberately fail-soft: a missing or
corrupt file falls back to defaults, and a failed write never crashes the app —
losing a preference is not worth interrupting the user.
"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".sagex"
CONFIG_FILE = CONFIG_DIR / "config.json"

# The hosted Autosage API. Override per-shell with SAGEX_API_URL (see api_url()), or
# persistently with "api_url" in config.json — e.g. a local dev stack on :8080.
DEFAULT_API_URL = "https://autosagex-api.duckdns.org"

# The Autosage web app — where users request a key and (until sagex v2) chat with Autobot.
WEB_URL = "https://autosagex.web.app"

# Every setting the app understands, with its default value.
DEFAULTS = {
    "shell": None,                        # None = auto-detect the default shell
    "api_url": DEFAULT_API_URL,
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


def api_url() -> str:
    """The backend base URL: SAGEX_API_URL, else config.json, else the hosted API.

    The env override is read here rather than merged in load(), so a later save()
    (e.g. the TUI remembering your shell) never writes it into config.json.
    """
    return os.environ.get("SAGEX_API_URL", "").strip() or load()["api_url"]


def resolve_workspace(path: str) -> str:
    """Expand + resolve a workspace path to an absolute one, or raise ValueError.

    Relative paths resolve against the current directory. Env vars and ~ expand.
    """
    resolved = os.path.abspath(os.path.expanduser(os.path.expandvars((path or "").strip())))
    if not os.path.isdir(resolved):
        raise ValueError(f"Not a folder: {resolved}")
    return resolved


def workspace_dir() -> str:
    """The effective workspace folder: the saved one, else the current directory."""
    return load().get("workspace") or os.getcwd()
