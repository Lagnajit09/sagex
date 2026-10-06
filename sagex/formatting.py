"""Presentation helpers — turning plain data into styled display objects.

Kept separate from data.py (what the data IS) and app.py (how the app behaves),
so all "how a status looks" decisions live in one place.
"""

import time
from datetime import datetime, timezone

from rich.text import Text


# --- Activity line (shared by the CLI and the TUI) -------------------------
# Claude-Code-style: a pulsing glyph, a shimmering verb, then elapsed time.
# Avoid glyphs with an emoji form (e.g. ✳ U+2733): terminals whose font lacks them
# fall back to a colour emoji font and flash a green square.
_PULSE = "·✢✶✻✽✻✶✢"
_ACCENT = "#d77757"
_ACCENT_HOT = "#f0b49a"


class RunActivity:
    """A live '✻ Running… (12s · ctrl+c to stop)' line. Re-render it on a timer;
    each render reflects the current time."""

    def __init__(self, verb: str = "Running", hint: str = "ctrl+c to stop") -> None:
        self.verb = verb
        self.hint = hint
        self._start = time.monotonic()

    def set(self, verb: str, hint: str | None = None) -> None:
        self.verb = verb
        if hint is not None:
            self.hint = hint

    def __rich__(self) -> Text:
        t = time.monotonic() - self._start
        line = Text()
        line.append(f"{_PULSE[int(t * 8) % len(_PULSE)]} ", style=_ACCENT)
        word = f"{self.verb}…"
        hot = int(t * 14) % (len(word) + 8) - 4
        for i, ch in enumerate(word):
            line.append(ch, style=f"bold {_ACCENT_HOT}" if abs(i - hot) <= 1 else _ACCENT)
        secs = int(t)
        elapsed = f"{secs}s" if secs < 60 else f"{secs // 60}m {secs % 60}s"
        line.append(f" ({elapsed} · {self.hint})" if self.hint else f" ({elapsed})",
                    style="bright_black")
        return line


def relative_time(iso: str | None) -> str:
    """Turn an ISO timestamp into a short human label: 'just now', '2m ago', '3h ago', '5d ago'."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


# --- Run status -> (icon, color). One place to change how a status looks. ---
STATUS_ICON = {
    "success":   ("✓", "green"),
    "completed": ("✓", "green"),            # script executions finish as 'completed'
    "failed":    ("✗", "red"),
    "running":   ("⟳", "yellow"),
    "queued":    ("◔", "yellow"),
    "cancelled": ("⊘", "bright_black"),
}
_DEFAULT_ICON = ("•", "white")   # for any status we don't recognize


def run_label(status: str, name: str, when: str) -> Text:
    """Build a colored tree label like:  ✓ Deploy Production · 2m ago

    Only the icon is colored; the rest stays default so it's readable.
    """
    icon, color = STATUS_ICON.get(status, _DEFAULT_ICON)
    label = Text()
    label.append(f"{icon} ", style=color)              # colored status icon
    label.append(f"{truncate_name(name)} · {when}")    # name (trimmed) + relative time
    return label


def truncate_name(name: str, max_len: int = 40) -> str:
    """Trim a NAME to max_len, adding … if longer.

    Only the name is trimmed — callers append metadata (e.g. '(server)', '· when',
    trigger type) AFTER this, so those identifiers are never cut off. A safety cap
    so long names can't blow out the narrow resources panel.
    """
    name = name or ""
    if len(name) <= max_len:
        return name
    return name[: max_len - 1].rstrip() + "…"


def trigger_label(name: str, detail: str, is_active: bool) -> Text:
    """Build a compact trigger label with an on/off dot:  ● Nightly Cleanup · 0 2 * * *

    Green ● = enabled, grey ○ = disabled. `detail` is the cron (schedule) or "http".
    """
    label = Text()
    if is_active:
        label.append("● ", style="green")        # enabled
    else:
        label.append("○ ", style="bright_black") # disabled
    label.append(f"{truncate_name(name)} · {detail}")   # name (trimmed) + type
    return label
