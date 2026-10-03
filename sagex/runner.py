"""Helpers for `sagex run`: which inputs a script needs, and following a live run."""

import queue
import re
import threading

# Same identifier rules as the web editor (client/src/utils/scriptParams.ts), so the
# CLI asks for exactly the variables the web Run drawer would.
_SCRIPT_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

# Mirror of the server's secret-name heuristic (execution_engine/helpers/params.py).
_SECRET_NAME_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|passphrase|credential)",
    re.IGNORECASE,
)

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


def script_params(content: str, meta: list | None) -> list[dict]:
    """The inputs a standalone run needs, in order of first appearance.

    One entry per distinct {{NAME}} in the body (case-insensitive, first casing
    wins), enriched with the script's saved metadata. Each entry is
    {name, type, default, secret, description}; secrets never carry a default.
    """
    meta_by_name = {
        str(m["name"]).lower(): m
        for m in meta or []
        if isinstance(m, dict) and m.get("name")
    }
    out: list[dict] = []
    seen: set[str] = set()
    for name in _SCRIPT_VAR_RE.findall(content or ""):
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        m = meta_by_name.get(key, {})
        ptype = m.get("type") or "string"
        secret = bool(m.get("secret")) or ptype == "password" or bool(_SECRET_NAME_RE.search(name))
        out.append({
            "name": name,
            "type": ptype,
            "default": "" if secret else (m.get("default") or ""),
            "secret": secret,
            "description": m.get("description") or "",
        })
    return out


def check_value(ptype: str, value: str) -> str | None:
    """Why `value` doesn't fit a number/boolean input, or None if it's fine.

    Blank is allowed for every type, matching what the web editor can send.
    """
    if not value.strip():
        return None
    if ptype == "number":
        try:
            float(value)
        except ValueError:
            return "expected a number"
    elif ptype == "boolean" and value.strip().lower() not in _TRUE | _FALSE:
        return "expected true/false"
    return None


class BackgroundStream:
    """Read an SSE frame iterator on a daemon thread.

    Ctrl+C is delivered to the main thread, so the HTTP connection survives it and
    the caller can send a stop request while still receiving the run's last frames.
    """

    _END = object()

    def __init__(self, frames) -> None:
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, args=(frames,), daemon=True).start()

    def _pump(self, frames) -> None:
        try:
            for frame in frames:
                self._queue.put(frame)
        except Exception as exc:
            self._queue.put(exc)
        finally:
            self._queue.put(self._END)

    def get(self):
        """Next (event, data) frame, or None when the stream ends; re-raises a
        stream error. Polls with a timeout because a blocking get() can't be
        interrupted by Ctrl+C on Windows."""
        while True:
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is self._END:
                return None
            if isinstance(item, Exception):
                raise item
            return item
