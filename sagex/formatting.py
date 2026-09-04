"""Presentation helpers — turning plain data into styled display objects.

Kept separate from data.py (what the data IS) and app.py (how the app behaves),
so all "how a status looks" decisions live in one place.
"""

from rich.text import Text


# --- Run status -> (icon, color). One place to change how a status looks. ---
STATUS_ICON = {
    "success":   ("✓", "green"),
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
