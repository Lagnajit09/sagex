"""RunGraph — a workflow run's live graph in the chat log.

Fed by the event lines `sagex run workflow` prints under the TUI; draws the same
compact graph the CLI pins at the bottom of the terminal, with running nodes pulsing.
"""

from textual.widgets import Static

from sagex.runview import WorkflowRunView


class RunGraph(Static):
    """Live graph for one run; call apply()/freeze() from the main thread."""

    DEFAULT_CSS = """
    RunGraph {
        width: 1fr;
        height: auto;
        padding: 0 1;
        margin-top: 1;
        border: round $panel-lighten-2;
        border-title-style: bold;
    }
    RunGraph.-success { border: round $success; }
    RunGraph.-failed { border: round $error; }
    """

    def __init__(self, view: WorkflowRunView) -> None:
        super().__init__()
        self.view = view
        self._timer = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(1 / 8, self._redraw)   # keeps running nodes pulsing
        self._redraw()

    def apply(self, event: dict) -> None:
        """Apply a run event; stops animating once the run has finished."""
        self.view.apply(event)
        self._redraw()
        if self.view.finished:
            self.freeze()

    def freeze(self) -> None:
        """Stop animating (the run ended, or the command did)."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._redraw()

    def _redraw(self) -> None:
        # Same frame as the CLI's panel: titled with the workflow, 'live' until the
        # run finishes, then the result (and a green/red border).
        self.border_title = self.view.name
        self.border_subtitle = self.view.status if self.view.finished else "live"
        self.set_class(self.view.status == "success", "-success")
        self.set_class(self.view.status == "failed", "-failed")
        self.update(self.view.graph())
