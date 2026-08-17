"""sagex — two-panel terminal app skeleton.

Run it with:
    d:\codingISFun\sagex-cli\.venv\Scripts\python.exe app.py
"""

from rich.text import Text                       # a string that can carry color/style

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Header, Footer, Static, Tree


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
    """

    # Keyboard shortcuts: (key, action, description-shown-in-footer).
    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    # Shown in the Autobot panel when NO real item is selected (the default).
    EMPTY_STATE = (
        "No item selected.\n\n"
        "Highlight a workflow or script to give Autobot context —\n"
        "or just start typing to ask anything."
    )

    def compose(self) -> ComposeResult:
        """Declare WHAT is on screen, top to bottom."""
        yield Header(show_clock=True)

        # A Horizontal container lays its children side by side (left -> right).
        with Horizontal():
            yield Tree("Resources", id="resources")     # the navigable list (left)
            yield Static(id="autobot")                   # right panel; filled in on_mount

        yield Footer()

    def on_mount(self) -> None:
        """Runs once, right after the widgets exist. Good place for setup."""
        autobot = self.query_one("#autobot", Static)
        autobot.border_title = "Autobot"
        autobot.update(self.EMPTY_STATE)        # start in the empty state

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
        autobot = self.query_one("#autobot", Static)

        # A branch (category header like "Workflows") CAN be expanded; a leaf
        # (a real item like "Deploy Production") CANNOT. `allow_expand` tells them apart.
        if node.allow_expand:
            autobot.update(self.EMPTY_STATE)                 # on a category -> empty state
        else:
            autobot.update(f"Selected: {str(node.label)}")   # on a real item -> show it


if __name__ == "__main__":
    sagex().run()
