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

try:
    from sagex._env import VALUES as _BAKED    # written from .env at build time (setup.py)
except ImportError:                            # a checkout that was never built/installed
    _BAKED = {}


def _baked(name: str) -> str:
    value = _BAKED.get(name)
    if not value:
        raise RuntimeError(
            f"{name} isn't configured. In the sagex checkout: cp .env.example .env, "
            "fill it in, then run: pip install -e ."
        )
    return value.rstrip("/")


# The hosted Autosage API. Override per-shell with SAGEX_API_URL (see api_url()), or
# persistently with "api_url" in config.json — e.g. a local dev stack on :8080.
DEFAULT_API_URL = _baked("SAGEX_API_URL")

# The Autosage web app — where users approve sign-ins, manage their sessions and
# their Autobot settings (e.g. a personal LLM key, which Sage uses too).
WEB_URL = _baked("SAGEX_WEB_URL")

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


def ai_url() -> str:
    """Where Autobot (and Sage) live: SAGEX_AI_URL, else `<api_url>/api/ai`.

    In prod and the local docker stack, nginx serves Autobot under /api/ai/ on the
    same host as the API, so the default is right; override only when running
    Autobot on its own port (e.g. SAGEX_AI_URL=http://localhost:8030).
    """
    return (os.environ.get("SAGEX_AI_URL", "").strip() or f"{api_url()}/api/ai").rstrip("/")


def resolve_workspace(path: str) -> str:
    """Expand + resolve a workspace path to an absolute one, or raise ValueError.

    Relative paths resolve against the current directory. Env vars and ~ expand.
    """
    resolved = os.path.abspath(os.path.expanduser(os.path.expandvars((path or "").strip())))
    if not os.path.isdir(resolved):
        raise ValueError(f"Not a folder: {resolved}")
    return resolved


def workspace_dir() -> str:
    """The effective workspace folder: the saved one, else the current directory.

    Every relative path a command takes resolves here (workspace_path), wherever
    sagex was started.
    """
    return load().get("workspace") or os.getcwd()


# Set (to "1") on the sagex commands Sage runs: every path they take must stay in
# the workspace, so a model can't push or render a file from anywhere on disk.
WORKSPACE_ONLY_ENV = "SAGEX_WORKSPACE_ONLY"


class OutsideWorkspace(ValueError):
    """A path a command was given isn't allowed (outside the workspace, for Sage)."""


def workspace_path(ref: str, workspace: str | os.PathLike | None = None) -> Path:
    """Where a path the user gave points: relative paths are under the workspace
    (never the current folder), absolute ones and ~ are used as given.

    Under SAGEX_WORKSPACE_ONLY=1 (Sage's runs), anything outside the workspace,
    or named like a secret file, raises OutsideWorkspace.
    """
    ws = Path(workspace if workspace is not None else workspace_dir())
    path = Path(os.path.expanduser(str(ref)))
    if not path.is_absolute():
        path = ws / path
    if os.environ.get(WORKSPACE_ONLY_ENV) == "1":
        real = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(ws))
        try:
            inside = os.path.commonpath([real, root]) == root
        except ValueError:                    # another drive on Windows
            inside = False
        if not inside:
            raise OutsideWorkspace(f"{ref} is outside the workspace ({ws}); only files in the "
                                   "workspace can be used here.")
        from sagex.local_tools import is_secret_name
        if is_secret_name(path.name):
            raise OutsideWorkspace(f"{path.name} usually holds secrets, so it can't be used here.")
    return path
