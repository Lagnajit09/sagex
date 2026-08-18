r"""sagex — two-panel terminal app skeleton.

Run it with:
    d:\codingISFun\sagex-cli\.venv\Scripts\python.exe app.py
"""

from rich.text import Text                       # a string that can carry color/style

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Tree, Input, RichLog


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


class sagex(App):
    """The whole application. Everything hangs off this one class."""

    # ---- Styling. Textual uses its own CSS dialect (TCSS). ----
    # We attach it right here as a string; later we'll move it to a .tcss file.
    CSS = """
    Horizontal {
        height: 1fr;                 /* fill space between header and footer */
    }

    /* One uniform background: make the header, footer, AND the tree all
       transparent so the single screen background shows through everywhere. */
    Header, Footer, Tree {
        background: transparent;
    }

    #resources {
        width: 30%;                  /* left panel takes 30% of the width */
        border: round $primary;
    }
    #autobot {
        width: 70%;                  /* right panel takes the remaining 70% */
        border: round $accent;
    }
    #chat-log {
        height: 1fr;                 /* fill all the space above the input box */
        padding: 0 1;                /* breathing room from the border */
    }
    """

    # Keyboard shortcuts: (key, action, description-shown-in-footer).
    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        """Declare WHAT is on screen, top to bottom."""
        yield Header(show_clock=True)

        # A Horizontal container lays its children side by side (left -> right).
        with Horizontal():
            yield Tree("Resources", id="resources")     # the navigable list (left)

            # The right side is now a VERTICAL stack: message area on top,
            # an input box docked at the bottom.
            with Vertical(id="autobot"):
                yield RichLog(id="chat-log", wrap=True, markup=False)   # scrolling messages
                yield Input(placeholder="Ask Autobot…  (Enter to send)", id="chat-input")

        yield Footer()

    def on_mount(self) -> None:
        """Runs once, right after the widgets exist. Good place for setup."""
        self.query_one("#autobot", Vertical).border_title = "Autobot"

        # Grab the Tree. The second argument (Tree) tells the editor/type-checker
        # exactly what kind of widget we got back, so it knows its methods.
        tree = self.query_one("#resources", Tree)
        tree.border_title = "Resources"
        tree.show_root = False      # hide the "Resources" root; show categories at top level

        # Every tree starts at `tree.root`. Adding to it builds the branches.
        # .add(...) makes an expandable branch and RETURNS it, so we can add children.
        # .add_leaf(...) adds a dead-end item (no arrow, can't expand).
        workflows = tree.root.add("Workflows")
        workflows.add_leaf("Deploy Production")
        workflows.add_leaf("Database Backup")
        workflows.add_leaf("Health Check")
        workflows.add_leaf("User Sync")
        workflows.add_leaf("Cleanup Logs")

        scripts = tree.root.add("Scripts")
        scripts.add_leaf("backup_postgres.sh")
        scripts.add_leaf("deploy.py")
        scripts.add_leaf("healthcheck.ps1")

        vault = tree.root.add("Vault")
        vault.add_leaf("prod-01 (server)")
        vault.add_leaf("prod-02 (server)")
        vault.add_leaf("aws-key (credential)")

        # Runs is the ONLY category with status icons — a run has a real outcome,
        # a workflow/script definition does not. Show just the 5 most recent.
        recent_runs = [
            ("running", "Health Check",      "just now"),
            ("success", "Deploy Production", "2m ago"),
            ("success", "Database Backup",   "1h ago"),
            ("failed",  "User Sync",         "3h ago"),
            ("success", "Cleanup Logs",      "6h ago"),
        ]
        runs = tree.root.add("Runs")
        for status, name, when in recent_runs:
            runs.add_leaf(run_label(status, name, when))

        tree.root.add("Triggers")

        tree.root.expand_all()      # start with everything opened up

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        """Fires whenever the highlight lands on a tree node (arrows or mouse).

        Textual routes a `Tree.NodeHighlighted` message here automatically because
        of this method's NAME: on_ + tree + node_highlighted.
        """
        node = event.node
        chat_input = self.query_one("#chat-input", Input)

        # A branch (category header like "Workflows") CAN be expanded; a leaf
        # (a real item like "Deploy Production") CANNOT. `allow_expand` tells them apart.
        # We show the current selection as a small "context" chip on the input's border.
        if node.allow_expand:
            chat_input.border_subtitle = ""                              # category -> no context
        else:
            chat_input.border_subtitle = f"context: {str(node.label)}"   # real item -> show it

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Fires when the user presses Enter in the input box."""
        text = event.value.strip()
        if not text:                       # ignore empty / whitespace-only sends
            return
        log = self.query_one("#chat-log", RichLog)

        # The user's message: a bold "You:" label followed by their text.
        user_msg = Text()
        user_msg.append("You: ", style="bold")
        user_msg.append(text)
        log.write(user_msg)

        # Autobot's reply — hardcoded for now; the network comes later.
        bot_msg = Text()
        bot_msg.append("Autobot: ", style="bold")
        bot_msg.append("(placeholder reply — I'm not connected to the server yet.)")
        log.write(bot_msg)

        log.write("")                      # blank line to separate exchanges
        event.input.value = ""             # clear the box for the next message


if __name__ == "__main__":
    sagex().run()
