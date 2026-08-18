"""Presentation helpers — turning plain data into styled display objects.

Kept separate from data.py (what the data IS) and app.py (how the app behaves),
so all "how a status looks" decisions live in one place.
"""

from rich.text import Text


# --- Run status -> (icon, color). One place to change how a status looks. ---
STATUS_ICON = {
    "success": ("✓", "green"),
    "failed":  ("✗", "red"),
    "running": ("⟳", "yellow"),
}


def run_label(status: str, name: str, when: str) -> Text:
    """Build a colored tree label like:  ✓ Deploy Production · 2m ago

    Only the icon is colored; the rest stays default so it's readable.
    """
    icon, color = STATUS_ICON[status]
    label = Text()
    label.append(f"{icon} ", style=color)        # colored status icon
    label.append(f"{name} · {when}")             # plain name + relative time
    return label
