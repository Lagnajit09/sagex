"""PromptInput — the chat/command input box, with ↑/↓ command history.

Subclasses Textual's Input to remember every submitted line and let you recall
older ones with Up, and step back toward the newest (or your unsent draft) with
Down — the behavior you expect from any shell prompt.
"""

from textual.widgets import Input


class PromptInput(Input):
    """An Input that recalls previously submitted lines with up/down."""

    # These merge with Input's built-in bindings; Input doesn't use up/down itself.
    BINDINGS = [
        ("up", "history_prev", "Prev"),
        ("down", "history_next", "Next"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._history: list[str] = []   # submitted lines, oldest -> newest
        self._index: int = 0            # points one past the end when not browsing
        self._draft: str = ""           # unsent text, restored when we browse back down

    def add_to_history(self, entry: str) -> None:
        """Record a submitted line (skips consecutive duplicates)."""
        if not self._history or self._history[-1] != entry:
            self._history.append(entry)
        self._index = len(self._history)   # reset the pointer to the end
        self._draft = ""

    def action_history_prev(self) -> None:
        """Up: move to an older entry."""
        if not self._history:
            return
        if self._index == len(self._history):
            self._draft = self.value       # save what we were typing before browsing
        self._index = max(0, self._index - 1)
        self._set_value(self._history[self._index])

    def action_history_next(self) -> None:
        """Down: move to a newer entry, or back to the unsent draft."""
        if self._index >= len(self._history):
            return                         # already at the draft line
        self._index += 1
        if self._index == len(self._history):
            self._set_value(self._draft)   # stepped past the newest -> restore draft
        else:
            self._set_value(self._history[self._index])

    def _set_value(self, value: str) -> None:
        self.value = value
        self.cursor_position = len(value)  # put the cursor at the end of the line
