"""ActivityLine — the TUI's Claude-Code-style loader ('✻ Running… (3s · esc to stop)').

Renders the same RunActivity the CLI shows on stderr, refreshed on a timer, and
hides itself when nothing is in progress.
"""

from textual.widgets import Static

from sagex.formatting import RunActivity


class ActivityLine(Static):
    """A one-line activity indicator; call start()/stop() from the main thread."""

    DEFAULT_CSS = """
    ActivityLine {
        height: 1;
        padding: 0 1;
        display: none;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._activity: RunActivity | None = None
        self._timer = None

    @property
    def active(self) -> bool:
        return self._activity is not None

    def start(self, verb: str, hint: str = "") -> None:
        """Show the line (restarting its timer) with `verb`, e.g. 'Running'."""
        self._activity = RunActivity(verb, hint)
        if self._timer is None:
            self._timer = self.set_interval(1 / 12, self._tick)
        self.display = True
        self._tick()

    def set(self, verb: str, hint: str | None = None) -> None:
        """Change the wording without resetting the timer."""
        if self._activity:
            self._activity.set(verb, hint)
            self._tick()

    def stop(self) -> None:
        """Hide the line."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._activity = None
        self.display = False

    def _tick(self) -> None:
        if self._activity:
            self.update(self._activity.__rich__())
