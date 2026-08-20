"""The sagex terminal application: layout and event wiring.

Appearance lives in app.tcss; mock data in data.py; status formatting in
formatting.py. This file focuses on WHAT widgets exist and HOW they react.
"""

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Tree, Input

from sagex import data
from sagex.formatting import run_label
from sagex.widgets.message import ChatMessage


class SagexApp(App):
    """The whole application. Everything hangs off this one class."""

    CSS_PATH = "app.tcss"                 # styling loaded from the file next to this one

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        """Declare WHAT is on screen, top to bottom."""
        yield Header(show_clock=True)

        # Horizontal split: navigable tree on the left, Autobot panel on the right.
        with Horizontal():
            yield Tree("Resources", id="resources")

            # The right side is a vertical stack: scrolling messages + input box.
            with Vertical(id="autobot"):
                yield VerticalScroll(id="chat-log")     # scrolls; holds ChatMessage widgets
                yield Input(placeholder="Ask Autobot…  (Enter to send)", id="chat-input")

        yield Footer()

    def on_mount(self) -> None:
        """Runs once, right after the widgets exist. Good place for setup."""
        self.query_one("#autobot", Vertical).border_title = "Autobot"

        tree = self.query_one("#resources", Tree)
        tree.border_title = "Resources"
        tree.show_root = False           # show categories at the top level

        # Build each category from the mock data in data.py.
        workflows = tree.root.add("Workflows")
        for name in data.WORKFLOWS:
            workflows.add_leaf(name)

        scripts = tree.root.add("Scripts")
        for name in data.SCRIPTS:
            scripts.add_leaf(name)

        vault = tree.root.add("Vault")
        for name in data.VAULT:
            vault.add_leaf(name)

        # Runs is the only category with status icons — a run has a real outcome.
        runs = tree.root.add("Runs")
        for status, name, when in data.RECENT_RUNS:
            runs.add_leaf(run_label(status, name, when))

        tree.root.add("Triggers")
        tree.root.expand_all()           # start with everything opened up

        # TEMP preview of all four message styles — remove when native commands
        # are wired up in the next step.
        self.add_message("Deploy the production build please", role="user")
        self.add_message("Sure — which environment did you have in mind?", role="autobot")
        self.add_message("docker ps --filter status=exited", role="command")
        self.add_message(
            "CONTAINER ID   IMAGE     STATUS\n3f9c2a1b       db:15     Exited (1) 2 minutes ago",
            role="cmd_output",
        )

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        """Fires whenever the highlight lands on a tree node (arrows or mouse)."""
        node = event.node
        chat_input = self.query_one("#chat-input", Input)

        # A branch (category) CAN be expanded; a leaf (real item) CANNOT. We show
        # the current selection as a small "context" chip on the input's border.
        if node.allow_expand:
            chat_input.border_subtitle = ""                              # category -> no context
        else:
            chat_input.border_subtitle = f"context: {str(node.label)}"   # real item -> show it

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Fires when the user presses Enter in the input box."""
        text = event.value.strip()
        if not text:                     # ignore empty / whitespace-only sends
            return
        self.add_message(text, role="user")
        # Autobot's reply — hardcoded for now; the network comes later.
        self.add_message(
            "(placeholder reply — I'm not connected to the server yet.)",
            role="autobot",
        )
        event.input.value = ""           # clear the box for the next message

    def add_message(self, text: str, role: str) -> None:
        """Append a chat message to the log and scroll it into view."""
        log = self.query_one("#chat-log", VerticalScroll)
        log.mount(ChatMessage(text, role))
        self.call_after_refresh(log.scroll_end)   # scroll after the new widget lays out
